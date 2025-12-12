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

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger("recall-websocket")
logger.setLevel(logging.INFO)


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
    """Represents a raw audio chunk from the meeting"""
    buffer: bytes  # PCM 16kHz mono, S16LE
    timestamp: float
    

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
    - audio_mixed_raw.data: Raw audio for custom processing
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
        on_participant_event: Optional[Callable[[str, ParticipantEvent], asyncio.Future]] = None,
    ):
        """
        Initialize the WebSocket server.
        
        Args:
            host: Server host address
            port: Server port
            on_transcript: Callback for transcript events (bot_id, utterance)
            on_audio_chunk: Callback for audio chunk events (bot_id, chunk)
            on_participant_event: Callback for participant events (bot_id, event)
        """
        self.host = host
        self.port = port
        self.on_transcript = on_transcript
        self.on_audio_chunk = on_audio_chunk
        self.on_participant_event = on_participant_event
        
        # Track active connections by bot_id
        self.connections: dict[str, WebSocketServerProtocol] = {}
        
        # Buffer for accumulating transcript (for full utterances)
        self.transcript_buffer: dict[str, list[TranscriptUtterance]] = {}
        
        # Current speaker tracking
        self.current_speakers: dict[str, set[int]] = {}
        
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
    
    async def _handle_connection(self, websocket: WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections from Recall.ai"""
        connection_id = f"conn_{id(websocket)}"
        logger.info(f"New WebSocket connection: {connection_id} from {websocket.remote_address}")
        
        bot_id = None
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    event_type = data.get("event", "")
                    event_data = data.get("data", {})
                    
                    # Extract bot_id from the event
                    if not bot_id:
                        bot_info = event_data.get("bot", {})
                        bot_id = bot_info.get("id")
                        if bot_id:
                            self.connections[bot_id] = websocket
                            self.transcript_buffer[bot_id] = []
                            self.current_speakers[bot_id] = set()
                            logger.info(f"Bot {bot_id} connected via WebSocket")
                    
                    # Route event to appropriate handler
                    await self._route_event(bot_id, event_type, event_data)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse WebSocket message: {e}")
                except Exception as e:
                    logger.error(f"Error processing WebSocket event: {e}", exc_info=True)
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"WebSocket connection closed: {connection_id} - {e}")
        finally:
            # Cleanup
            if bot_id:
                self.connections.pop(bot_id, None)
                self.transcript_buffer.pop(bot_id, None)
                self.current_speakers.pop(bot_id, None)
                logger.info(f"Bot {bot_id} disconnected")
    
    async def _route_event(self, bot_id: Optional[str], event_type: str, event_data: dict):
        """Route event to the appropriate handler"""
        
        if event_type == "transcript.data":
            await self._handle_transcript(bot_id, event_data, is_partial=False)
            
        elif event_type == "transcript.partial_data":
            await self._handle_transcript(bot_id, event_data, is_partial=True)
            
        elif event_type == "audio_mixed_raw.data":
            await self._handle_audio(bot_id, event_data)
            
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
        """Handle raw audio events"""
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
    
    async def _handle_participant_event(self, bot_id: Optional[str], event_name: str, event_data: dict):
        """Handle participant events"""
        data = event_data.get("data", {})
        participant = data.get("participant", {})
        timestamp_data = data.get("timestamp", {})
        
        event = ParticipantEvent(
            event_type=event_name,
            participant_id=participant.get("id", 0),
            participant_name=participant.get("name"),
            timestamp=timestamp_data.get("relative", 0.0),
        )
        
        # Track current speakers
        if bot_id:
            if event_name == "speech_on":
                self.current_speakers.setdefault(bot_id, set()).add(event.participant_id)
            elif event_name == "speech_off":
                self.current_speakers.get(bot_id, set()).discard(event.participant_id)
        
        speaker = event.participant_name or f"Participant {event.participant_id}"
        logger.info(f"[PARTICIPANT] {event_name}: {speaker}")
        
        # Call participant event callback
        if self.on_participant_event and bot_id:
            try:
                await self.on_participant_event(bot_id, event)
            except Exception as e:
                logger.error(f"Error in participant event callback: {e}", exc_info=True)
    
    def is_someone_speaking(self, bot_id: str) -> bool:
        """Check if anyone is currently speaking in the meeting"""
        return bool(self.current_speakers.get(bot_id, set()))
    
    def get_current_speakers(self, bot_id: str) -> set[int]:
        """Get the set of currently speaking participant IDs"""
        return self.current_speakers.get(bot_id, set()).copy()


async def run_websocket_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    on_transcript: Optional[Callable] = None,
    on_audio_chunk: Optional[Callable] = None,
    on_participant_event: Optional[Callable] = None,
):
    """
    Convenience function to run the WebSocket server.
    
    Example:
        async def handle_transcript(bot_id, utterance):
            print(f"Got transcript: {utterance.text}")
            # Process with LLM...
        
        await run_websocket_server(on_transcript=handle_transcript)
    """
    server = RecallWebSocketServer(
        host=host,
        port=port,
        on_transcript=on_transcript,
        on_audio_chunk=on_audio_chunk,
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
    
    async def example_participant_handler(bot_id: str, event: ParticipantEvent):
        if event.event_type == "join":
            print(f"👋 {event.participant_name} joined the meeting")
        elif event.event_type == "leave":
            print(f"👋 {event.participant_name} left the meeting")
    
    asyncio.run(run_websocket_server(
        on_transcript=example_transcript_handler,
        on_participant_event=example_participant_handler,
    ))
