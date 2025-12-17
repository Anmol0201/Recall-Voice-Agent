"""
WebSocket Server - Receives real-time audio and transcripts from Recall.ai.
Processes audio through your LLM pipeline and sends responses back.
"""
import asyncio
import base64
import json
import logging
from typing import Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime
from assistant import transcribe_base64_pcm, has_speech

import numpy as np
import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger("recall-websocket")
logger.setLevel(logging.INFO)

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MIN_TRANSCRIBE_SECONDS = 0.8
MIN_TRANSCRIBE_BYTES = int(MIN_TRANSCRIBE_SECONDS * BYTES_PER_SECOND)
MAX_BUFFER_SECONDS = 5
MAX_BUFFER_BYTES = int(MAX_BUFFER_SECONDS * BYTES_PER_SECOND)

# Silence detection settings
SILENCE_THRESHOLD = 1500  # RMS amplitude threshold for silence (raised from 500)
SILENCE_DURATION_MS = 800  # Trigger transcription after this much silence (ms)
SILENCE_CHUNKS_REQUIRED = int(SILENCE_DURATION_MS / 200)  # ~200ms per chunk from Recall

def concat_pcm(chunks):
    return b"".join(c.buffer for c in chunks)


def is_silent_chunk(pcm_bytes: bytes, threshold: int = SILENCE_THRESHOLD) -> tuple[bool, float]:
    """Check if an audio chunk is silent based on RMS amplitude."""
    if len(pcm_bytes) < 2:
        return True, 0.0
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    return rms < threshold, rms  # Return both result and RMS value


def looks_like_speech(text: str) -> bool:
    if not text:
        return False
    if len(text) < 3:
        return False
    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.6:
        return False
    if len(text.split()) == 1 and len(text) <= 3:
        return False
    return True


@dataclass
class TranscriptUtterance:
    """Represents a transcript utterance from a participant"""
    text: str
    participant_id: int
    participant_name: Optional[str]
    is_partial: bool
    timestamp: float
    

@dataclass 
class AudioChunk:
    """Represents a raw audio chunk from the meeting (mixed audio)"""
    buffer: bytes  # PCM 16kHz mono, S16LE
    timestamp: float


@dataclass
class ParticipantAudioChunk:
    """Represents a raw audio chunk from a specific participant (separate audio)"""
    buffer: bytes  # PCM 16kHz mono, S16LE
    timestamp: float
    absolute_timestamp: Optional[str]
    participant_id: int
    participant_name: Optional[str]
    is_host: bool
    platform: Optional[str]
    email: Optional[str]
    extra_data: dict = field(default_factory=dict)


@dataclass
class ParticipantEvent:
    """Represents a participant event"""
    event_type: str  # join, leave, speech_on, speech_off
    participant_id: int
    participant_name: Optional[str]
    timestamp: float


class RecallWebSocketServer:
    """
    WebSocket server that receives real-time data from Recall.ai.
    
    Handles:
    - transcript.data / transcript.partial_data: Speech-to-text from meeting
    - audio_mixed_raw.data: Raw mixed audio for custom processing
    - audio_separate_raw.data: Per-participant raw audio
    - participant_events.*: Speaker detection and meeting events
    
    Flow:
    1. Recall.ai connects to this WebSocket when bot joins meeting
    2. Real-time transcript/audio events stream in
    3. Events are processed and forwarded to your LLM pipeline
    4. Responses are sent back to Recall.ai via API (output_audio)
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        on_transcript: Optional[Callable[[str, TranscriptUtterance], asyncio.Future]] = None,
        on_audio_chunk: Optional[Callable[[str, AudioChunk], asyncio.Future]] = None,
        on_participant_audio: Optional[Callable[[str, ParticipantAudioChunk], asyncio.Future]] = None,
        on_participant_event: Optional[Callable[[str, ParticipantEvent], asyncio.Future]] = None,
    ):
        """
        Initialize the WebSocket server.
        
        Args:
            host: Server host address
            port: Server port
            on_transcript: Callback for transcript events (bot_id, utterance)
            on_audio_chunk: Callback for mixed audio chunk events (bot_id, chunk)
            on_participant_audio: Callback for per-participant audio events (bot_id, chunk)
            on_participant_event: Callback for participant events (bot_id, event)
        """
        self.host = host
        self.port = port
        self.on_transcript = on_transcript
        self.on_audio_chunk = on_audio_chunk
        self.on_participant_audio = on_participant_audio
        self.on_participant_event = on_participant_event
        
        # Track active connections by bot_id
        self.connections: dict[str, WebSocketServerProtocol] = {}
        
        # Buffer for accumulating transcript (for full utterances)
        self.transcript_buffer: dict[str, list[TranscriptUtterance]] = {}
        
        # Current speaker tracking
        self.current_speakers: dict[str, set[int]] = {}
        
        # Per-participant audio buffers (for accumulating audio per speaker)
        self.participant_audio_buffers: dict[str, dict[int, list[ParticipantAudioChunk]]] = {}
        
        # Track consecutive silent chunks per participant for auto-segmentation
        self.silence_counters: dict[str, dict[int, int]] = {}
        
        # Lock to prevent concurrent processing of same participant's audio
        self.processing_locks: dict[str, dict[int, bool]] = {}
        
        self.server = None
        self._running = False
        
        logger.info(f"RecallWebSocketServer initialized on {host}:{port}")
    
    async def start(self):
        """Start the WebSocket server"""
        self.server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
        )
        self._running = True
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
    
    async def stop(self):
        """Stop the WebSocket server"""
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("WebSocket server stopped")
    
    async def _handle_connection(self, websocket: WebSocketServerProtocol):
        """Handle incoming WebSocket connections from Recall.ai"""
        connection_id = f"conn_{id(websocket)}"
        logger.info(
            f"New WebSocket connection: {connection_id} "
            f"from {websocket.remote_address}"
        )

        bot_id = None

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    event_type = data.get("event", "")
                    event_data = data.get("data", {})

                    # Extract bot_id from the first event
                    if not bot_id:
                        bot_info = event_data.get("bot", {})
                        bot_id = bot_info.get("id")
                        if bot_id:
                            self.connections[bot_id] = websocket
                            self.transcript_buffer[bot_id] = []
                            self.current_speakers[bot_id] = set()
                            self.participant_audio_buffers[bot_id] = {}
                            logger.info(f"Bot {bot_id} connected via WebSocket")

                    await self._route_event(bot_id, event_type, event_data)

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse WebSocket message: {e}")
                except Exception as e:
                    logger.error(
                        f"Error processing WebSocket event: {e}",
                        exc_info=True,
                    )

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"WebSocket connection closed: {connection_id} - {e}")

        finally:
            if bot_id:
                self.connections.pop(bot_id, None)
                self.transcript_buffer.pop(bot_id, None)
                self.current_speakers.pop(bot_id, None)
                self.participant_audio_buffers.pop(bot_id, None)
                logger.info(f"Bot {bot_id} disconnected")

    
    async def _route_event(self, bot_id: Optional[str], event_type: str, event_data: dict):
        """Route event to the appropriate handler"""
        

        
        if event_type == "transcript.data":
            await self._handle_transcript(bot_id, event_data, is_partial=False)
            
        elif event_type == "transcript.partial_data":
            await self._handle_transcript(bot_id, event_data, is_partial=True)
            
        elif event_type == "audio_mixed_raw.data":
            await self._handle_audio(bot_id, event_data)
            
        elif event_type == "audio_separate_raw.data":
            await self._handle_separate_audio(bot_id, event_data)
            
        elif event_type.startswith("participant_events."):
            event_name = event_type.replace("participant_events.", "")
            await self._handle_participant_event(bot_id, event_name, event_data)
            
        else:
            logger.debug(f"Unhandled event type: {event_type}")
    
    async def _handle_transcript(self, bot_id: Optional[str], event_data: dict, is_partial: bool):
        """Handle transcript events"""
        data = event_data.get("data", {})
        words = data.get("words", [])
        participant = data.get("participant", {})
        
        # Extract text from words
        text = " ".join(word.get("text", "") for word in words)
        
        if not text.strip():
            return
        
        # Get timestamp
        timestamp = 0.0
        if words:
            start_ts = words[0].get("start_timestamp", {})
            timestamp = start_ts.get("relative", 0.0)
        
        utterance = TranscriptUtterance(
            text=text,
            participant_id=participant.get("id", 0),
            participant_name=participant.get("name"),
            is_partial=is_partial,
            timestamp=timestamp,
        )
        
        event_label = "PARTIAL" if is_partial else "FINAL"
        speaker = utterance.participant_name or f"Participant {utterance.participant_id}"
        logger.info(f"[{event_label}] {speaker}: {text}")
        
        # Call transcript callback
        if self.on_transcript and bot_id:
            try:
                await self.on_transcript(bot_id, utterance)
            except Exception as e:
                logger.error(f"Error in transcript callback: {e}", exc_info=True)
    
    async def _handle_audio(self, bot_id: Optional[str], event_data: dict):
        """Handle raw mixed audio events"""
        data = event_data.get("data", {})
        
        # Audio is base64-encoded raw PCM: 16kHz mono, S16LE
        buffer_b64 = data.get("buffer", "")
        if not buffer_b64:
            return
        
        try:
            buffer_bytes = base64.b64decode(buffer_b64)
        except Exception as e:
            logger.error(f"Failed to decode audio buffer: {e}")
            return
        
        timestamp_data = data.get("timestamp", {})
        timestamp = timestamp_data.get("relative", 0.0)
        
        chunk = AudioChunk(
            buffer=buffer_bytes,
            timestamp=timestamp,
        )
        
        # Call audio callback
        if self.on_audio_chunk and bot_id:
            try:
                await self.on_audio_chunk(bot_id, chunk)
            except Exception as e:
                logger.error(f"Error in audio callback: {e}", exc_info=True)
    
    async def _handle_separate_audio(self, bot_id: Optional[str], event_data: dict):
        """Handle per-participant raw audio events (audio_separate_raw.data)"""

        data = event_data.get("data", {})
        buffer_b64 = data.get("buffer", "")
        if not buffer_b64 or not bot_id:
            return

        try:
            buffer_bytes = base64.b64decode(buffer_b64)
        except Exception as e:
            logger.error(f"Failed to decode audio buffer: {e}")
            return


        timestamp_data = data.get("timestamp", {})
        participant = data.get("participant", {})

        participant_id = participant.get("id", 0)
        participant_name = participant.get("name") or f"Participant {participant_id}"

        chunk = ParticipantAudioChunk(
            buffer=buffer_bytes,
            timestamp=timestamp_data.get("relative", 0.0),
            absolute_timestamp=timestamp_data.get("absolute"),
            participant_id=participant_id,
            participant_name=participant_name,
            is_host=participant.get("is_host", False),
            platform=participant.get("platform"),
            email=participant.get("email"),
            extra_data=participant.get("extra_data", {}),
        )

        # ---- BUFFER AUDIO ----
        self.participant_audio_buffers.setdefault(bot_id, {})
        self.participant_audio_buffers[bot_id].setdefault(participant_id, [])
        self.silence_counters.setdefault(bot_id, {})
        self.silence_counters[bot_id].setdefault(participant_id, 0)
        self.processing_locks.setdefault(bot_id, {})
        self.processing_locks[bot_id].setdefault(participant_id, False)

        buf = self.participant_audio_buffers[bot_id][participant_id]
        buf.append(chunk)

        # ---- CAP BUFFER (~5s) ----
        total_bytes = sum(len(c.buffer) for c in buf)
        while total_bytes > MAX_BUFFER_BYTES:
            removed = buf.pop(0)
            total_bytes -= len(removed.buffer)

        # Log first chunk per participant to debug ID matching
        if len(buf) == 1:
            # logger.info(
            #     f"[BUFFER_START] participant_id={participant_id}, "
            #     f"name={participant_name}"
            # )
            pass
            

        # ---- SILENCE DETECTION ----
        is_silent, rms = is_silent_chunk(buffer_bytes)
        
        # Log RMS periodically (every 10th chunk to avoid spam)
        if len(buf) % 10 == 0:
            # logger.info(f"[RMS] {participant_name}: RMS={rms:.0f}, threshold={SILENCE_THRESHOLD}, silent={is_silent}")
            pass
        
        if is_silent:
            self.silence_counters[bot_id][participant_id] += 1
        else:
            self.silence_counters[bot_id][participant_id] = 0

        # ---- TRIGGER ON SILENCE ----
        silence_count = self.silence_counters[bot_id][participant_id]
        is_processing = self.processing_locks[bot_id][participant_id]
        has_enough_audio = total_bytes >= MIN_TRANSCRIBE_BYTES

        if silence_count >= SILENCE_CHUNKS_REQUIRED and has_enough_audio and not is_processing:
            # logger.info(f"[SILENCE] Detected {silence_count} silent chunks for {participant_name}, processing...")
            # Reset silence counter
            self.silence_counters[bot_id][participant_id] = 0
            # Process in background to not block audio handling
            asyncio.create_task(self._process_participant_audio(bot_id, participant_id, participant_name))

    async def _process_participant_audio(
        self,
        bot_id: str,
        participant_id: int,
        participant_name: str,
    ):
        """Process buffered audio for a participant - shared by silence detection and speech_off"""
        
        # Acquire processing lock
        self.processing_locks.setdefault(bot_id, {})
        if self.processing_locks.get(bot_id, {}).get(participant_id, False):
            # logger.debug(f"[SKIP] Already processing audio for {participant_name}")
            return
        self.processing_locks[bot_id][participant_id] = True
        
        try:
            # ---- FETCH BUFFERED AUDIO ----
            bot_buffers = self.participant_audio_buffers.get(bot_id, {})
            buffers = bot_buffers.get(participant_id)

            if not buffers:
                # logger.debug(f"[SKIP] No audio buffered for {participant_name}")
                return

            pcm = b"".join(chunk.buffer for chunk in buffers)
            duration_sec = len(pcm) / BYTES_PER_SECOND
            # logger.info(f"[PROCESS] {participant_name}: {duration_sec:.2f}s ({len(pcm)} bytes)")

            # Clear buffer immediately
            self.participant_audio_buffers[bot_id][participant_id] = []

            # ---- MINIMUM DURATION CHECK ----
            if len(pcm) < MIN_TRANSCRIBE_BYTES:
                logger.info(
                    # f"[SKIP] Dropped short utterance from {participant_name} "
                    f"({duration_sec:.2f}s < {MIN_TRANSCRIBE_SECONDS}s)"
                )
                return

            # ---- VAD ON FULL UTTERANCE (non-blocking) ----
            # logger.info(f"[VAD] Running VAD for {participant_name}...")
            try:
                has_voice = await asyncio.to_thread(has_speech, pcm)
                if not has_voice:
                    # logger.info(f"[SKIP] VAD rejected utterance from {participant_name}")
                    return
                logger.info(f"[VAD] Speech detected for {participant_name}")
            except Exception as e:
                logger.error(f"[ERROR] VAD failed: {e}", exc_info=True)
                return

            # ---- TRANSCRIBE (non-blocking) ----
            logger.info(f"[ASR] Sending to Whisper for {participant_name}...")
            try:
                text = await asyncio.to_thread(transcribe_base64_pcm, pcm)
                logger.info(f"[ASR] Whisper returned: '{text}'")
            except Exception as e:
                logger.error(f"[ERROR] ASR failed: {e}", exc_info=True)
                return

            if not text or not looks_like_speech(text):
                logger.info(f"[SKIP] Dropped non-speech text: '{text}'")
                return

            # ---- FINAL OUTPUT ----
            logger.info(f"Speaker {participant_name}: {text}")

            # Optional downstream hook
            if self.on_transcript:
                utterance = TranscriptUtterance(
                    text=text,
                    participant_id=participant_id,
                    participant_name=participant_name,
                    is_partial=False,
                    timestamp=0.0,
                )
                try:
                    await self.on_transcript(bot_id, utterance)
                except Exception as e:
                    logger.error(f"Error in transcript callback: {e}", exc_info=True)
                    
        finally:
            # Release processing lock
            self.processing_locks[bot_id][participant_id] = False

    
    async def _handle_participant_event(
    self,
    bot_id: Optional[str],
    event_name: str,
    event_data: dict,
    ):
        """Handle participant events (speech_on / speech_off)"""

        if not bot_id:
            return

        data = event_data.get("data", {})
        participant = data.get("participant", {})
        timestamp_data = data.get("timestamp", {})

        participant_id = participant.get("id", 0)
        participant_name = participant.get("name") or f"Participant {participant_id}"

        event = ParticipantEvent(
            event_type=event_name,
            participant_id=participant_id,
            participant_name=participant_name,
            timestamp=timestamp_data.get("relative", 0.0),
        )

        # ===============================
        # SPEECH START
        # ===============================
        if event_name == "speech_on":
            self.current_speakers.setdefault(bot_id, set()).add(participant_id)
            logger.info(f"[SPEECH_ON] participant_id={participant_id}, name={participant_name}")
            return

        # ===============================
        # ONLY HANDLE SPEECH END
        # ===============================
        if event_name != "speech_off":
            return

        self.current_speakers.get(bot_id, set()).discard(participant_id)
        logger.info(f"[SPEECH_OFF] participant_id={participant_id}, name={participant_name}")
        
        # Reset silence counter and process any remaining audio
        self.silence_counters.setdefault(bot_id, {})
        self.silence_counters[bot_id][participant_id] = 0
        
        # Process audio (will handle empty buffer gracefully)
        await self._process_participant_audio(bot_id, participant_id, participant_name)

        # Optional downstream hook for the event itself
        if self.on_participant_event:
            await self.on_participant_event(bot_id, event)


    
    def is_someone_speaking(self, bot_id: str) -> bool:
        """Check if anyone is currently speaking in the meeting"""
        return bool(self.current_speakers.get(bot_id, set()))
    
    def get_current_speakers(self, bot_id: str) -> set[int]:
        """Get the set of currently speaking participant IDs"""
        return self.current_speakers.get(bot_id, set()).copy()
    
    def get_participant_audio_buffer(self, bot_id: str, participant_id: int) -> list[ParticipantAudioChunk]:
        """Get accumulated audio chunks for a specific participant"""
        return self.participant_audio_buffers.get(bot_id, {}).get(participant_id, []).copy()
    
    def clear_participant_audio_buffer(self, bot_id: str, participant_id: int):
        """Clear the audio buffer for a specific participant"""
        if bot_id in self.participant_audio_buffers:
            if participant_id in self.participant_audio_buffers[bot_id]:
                self.participant_audio_buffers[bot_id][participant_id] = []


async def run_websocket_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    on_transcript: Optional[Callable] = None,
    on_audio_chunk: Optional[Callable] = None,
    on_participant_audio: Optional[Callable] = None,
    on_participant_event: Optional[Callable] = None,
):
    """
    Convenience function to run the WebSocket server.
    
    Example:
        async def handle_transcript(bot_id, utterance):
            print(f"Got transcript: {utterance.text}")
            # Process with LLM...
        
        async def handle_participant_audio(bot_id, chunk):
            print(f"Got audio from {chunk.participant_name}: {len(chunk.buffer)} bytes")
            # Process per-participant audio...
        
        await run_websocket_server(
            on_transcript=handle_transcript,
            on_participant_audio=handle_participant_audio,
        )
    """
    server = RecallWebSocketServer(
        host=host,
        port=port,
        on_transcript=on_transcript,
        on_audio_chunk=on_audio_chunk,
        on_participant_audio=on_participant_audio,
        on_participant_event=on_participant_event,
    )
    
    await server.start()
    
    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    # Example usage
    async def example_transcript_handler(bot_id: str, utterance: TranscriptUtterance):
        if not utterance.is_partial:
            print(f"\n🎤 [{utterance.participant_name}]: {utterance.text}\n")
    
    async def example_participant_audio_handler(bot_id: str, chunk: ParticipantAudioChunk):
        speaker = chunk.participant_name or f"Participant {chunk.participant_id}"
        print(f"🔊 [{speaker}]: {len(chunk.buffer)} bytes @ {chunk.timestamp:.2f}s")
    
    async def example_participant_handler(bot_id: str, event: ParticipantEvent):
        if event.event_type == "join":
            print(f"👋 {event.participant_name} joined the meeting")
        elif event.event_type == "leave":
            print(f"👋 {event.participant_name} left the meeting")
    
    asyncio.run(run_websocket_server(
        on_transcript=example_transcript_handler,
        on_participant_audio=example_participant_audio_handler,
        on_participant_event=example_participant_handler,
    ))