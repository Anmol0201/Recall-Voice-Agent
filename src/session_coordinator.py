"""
Session Coordinator - Orchestrates the complete voice AI pipeline with Recall.ai.
Bridges Recall.ai events with your LLM pipeline and handles audio I/O.
"""
import asyncio
import base64
import io
import logging
import os
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from dotenv import load_dotenv

# Import Google TTS for audio generation (same as your existing setup)
try:
    from google.cloud import texttospeech
    GOOGLE_TTS_AVAILABLE = True
except ImportError:
    GOOGLE_TTS_AVAILABLE = False

# Import your existing LLM
from llm import AgentLLM

# Import Recall components  
from recall_manager import RecallBotManager, BotSession, BotStatus
from recall_websocket import RecallWebSocketServer, TranscriptUtterance, ParticipantEvent

# Import RecallLiveKitBridge for audio processing
from recall_livekit_bridge import RecallLiveKitBridge

logger = logging.getLogger("session-coordinator")
logger.setLevel(logging.INFO)

load_dotenv(".env.local")


class SessionState(Enum):
    """States for a voice session"""
    INITIALIZING = "initializing"
    WAITING_FOR_BOT = "waiting_for_bot"
    ACTIVE = "active"
    PROCESSING = "processing"  # Bot heard something, LLM is generating response
    RESPONDING = "responding"  # Bot is speaking
    PAUSED = "paused"
    ENDED = "ended"


@dataclass
class VoiceSession:
    """Tracks a voice AI session with a meeting"""
    bot_id: str
    meeting_url: str
    state: SessionState = SessionState.INITIALIZING
    created_at: datetime = field(default_factory=datetime.now)
    
    # Conversation history for context
    conversation_history: list = field(default_factory=list)
    
    # Current processing state
    current_utterance: Optional[str] = None
    is_processing: bool = False
    last_response_time: Optional[datetime] = None
    
    # Accumulated transcript (builds up partial results)
    pending_transcript: str = ""
    last_transcript_time: Optional[datetime] = None


class SessionCoordinator:
    """
    Coordinates the voice AI pipeline:
    
    1. Receives transcripts from Recall.ai WebSocket
    2. Detects end of speech (silence/pause)
    3. Sends to LLM for processing
    4. Converts response to audio via TTS
    5. Sends audio back to Recall.ai to speak in meeting
    
    This bridges your existing LLM setup with Recall.ai's meeting bot.
    """
    
    def __init__(
        self,
        recall_api_key: str,
        websocket_host: str = "0.0.0.0",
        websocket_port: int = 8765,
        webhook_url: Optional[str] = None,
        llm_api_url: Optional[str] = None,
        google_credentials_file: Optional[str] = None,
        recall_region: str = "us-east-1",
    ):
        """
        Initialize the session coordinator.
        
        Args:
            recall_api_key: Recall.ai API key
            websocket_host: Host for WebSocket server
            websocket_port: Port for WebSocket server
            webhook_url: Public URL for Recall.ai webhooks
            llm_api_url: Your LLM API URL (if using custom LLM)
            google_credentials_file: Path to Google Cloud credentials for TTS
            recall_region: Recall.ai API region
        """
        self.websocket_host = websocket_host
        self.websocket_port = websocket_port
        
        # Get public WebSocket URL (you'll need to set this to your public URL)
        public_ws_url = os.getenv("PUBLIC_WEBSOCKET_URL", f"ws://localhost:{websocket_port}")
        
        # Initialize Recall bot manager
        self.recall_manager = RecallBotManager(
            api_key=recall_api_key,
            region=recall_region,
            websocket_url=public_ws_url,
            webhook_url=webhook_url,
        )
        
        # Initialize WebSocket server with callbacks
        self.websocket_server = RecallWebSocketServer(
            host=websocket_host,
            port=websocket_port,
            on_transcript=self._handle_transcript,
            on_participant_event=self._handle_participant_event,
        )
        
        # Initialize LLM (use your existing setup or custom API)
        self.llm_api_url = llm_api_url or os.getenv("AGENT_API_URL")
        self.llm = None
        if self.llm_api_url:
            self.llm = AgentLLM(
                api_url=self.llm_api_url,
                userid="recall_voice_agent",
                mode="brief",
                source="voice",
                timeout=60,
            )
        
        # Initialize Google TTS
        self.google_credentials_file = google_credentials_file or os.getenv("GOOGLE_CREDENTIALS_FILE")
        self.tts_client = None
        if GOOGLE_TTS_AVAILABLE and self.google_credentials_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.google_credentials_file
            self.tts_client = texttospeech.TextToSpeechClient()
        
        # Initialize RecallLiveKitBridge for audio processing
        self.audio_bridge = RecallLiveKitBridge(
            bot_manager=self.recall_manager,
            llm_api_url=self.llm_api_url,
        )
        logger.info("Initialized RecallLiveKitBridge for audio processing")
        
        # Active sessions
        self.sessions: dict[str, VoiceSession] = {}
        
        # Processing configuration
        self.silence_threshold = 1.5  # Seconds of silence before processing
        self.min_utterance_length = 3  # Minimum characters to process
        
        # Background tasks
        self._processing_tasks: dict[str, asyncio.Task] = {}
        
        logger.info("SessionCoordinator initialized")
    
    async def start(self):
        """Start all services"""
        logger.info("Starting SessionCoordinator...")
        
        # Start WebSocket server
        await self.websocket_server.start()
        
        # Start silence detection loop
        asyncio.create_task(self._silence_detection_loop())
        
        logger.info("SessionCoordinator started successfully")
    
    async def stop(self):
        """Stop all services"""
        logger.info("Stopping SessionCoordinator...")
        
        # Cancel all processing tasks
        for task in self._processing_tasks.values():
            task.cancel()
        
        # Leave all meetings
        for bot_id in list(self.sessions.keys()):
            try:
                await self.recall_manager.leave_meeting(bot_id)
            except Exception as e:
                logger.error(f"Error leaving meeting {bot_id}: {e}")
        
        # Stop WebSocket server
        await self.websocket_server.stop()
        
        logger.info("SessionCoordinator stopped")
    
    async def join_meeting(
        self,
        meeting_url: str,
        bot_name: str = "Jarvis",
    ) -> VoiceSession:
        """
        Join a Google Meet meeting with the voice AI bot.
        
        Args:
            meeting_url: Google Meet URL
            bot_name: Display name for the bot
            
        Returns:
            VoiceSession: The created session
        """
        logger.info(f"Joining meeting: {meeting_url} as {bot_name}")
        
        # Create bot via Recall.ai
        bot_session = await self.recall_manager.create_bot(
            meeting_url=meeting_url,
            bot_name=bot_name,
            enable_transcription=True,
            enable_audio_streaming=False,  # Using transcription instead
        )
        
        # Create voice session
        session = VoiceSession(
            bot_id=bot_session.bot_id,
            meeting_url=meeting_url,
            state=SessionState.WAITING_FOR_BOT,
        )
        self.sessions[bot_session.bot_id] = session
        
        logger.info(f"Created session for bot: {bot_session.bot_id}")
        return session
    
    async def leave_meeting(self, bot_id: str):
        """Leave a meeting"""
        if bot_id in self.sessions:
            self.sessions[bot_id].state = SessionState.ENDED
        
        await self.recall_manager.leave_meeting(bot_id)
        await self.cleanup_session(bot_id)
    
    async def register_session(self, bot_id: str, meeting_url: str):
        """Register a session (called from API)"""
        if bot_id not in self.sessions:
            self.sessions[bot_id] = VoiceSession(
                bot_id=bot_id,
                meeting_url=meeting_url,
                state=SessionState.WAITING_FOR_BOT,
            )
    
    async def handle_status_change(self, bot_id: str, status: str):
        """Handle bot status change from webhook"""
        session = self.sessions.get(bot_id)
        if not session:
            return
        
        logger.info(f"Bot {bot_id} status changed to: {status}")
        
        if status == "in_call_recording":
            session.state = SessionState.ACTIVE
            logger.info(f"Session {bot_id} is now ACTIVE")
            
        elif status in ["call_ended", "done", "fatal"]:
            session.state = SessionState.ENDED
            await self.cleanup_session(bot_id)
    
    async def cleanup_session(self, bot_id: str):
        """Clean up a session"""
        # Cancel processing task
        if bot_id in self._processing_tasks:
            self._processing_tasks[bot_id].cancel()
            del self._processing_tasks[bot_id]
        
        # Remove session
        self.sessions.pop(bot_id, None)
        logger.info(f"Cleaned up session: {bot_id}")
    
    # ========================================================================
    # Event Handlers
    # ========================================================================
    
    async def _handle_transcript(self, bot_id: str, utterance: TranscriptUtterance):
        """Handle incoming transcript from Recall.ai"""
        session = self.sessions.get(bot_id)
        if not session or session.state not in [SessionState.ACTIVE, SessionState.PROCESSING]:
            return
        
        # Skip if bot is currently responding
        if session.state == SessionState.RESPONDING:
            return
        
        # Handle partial vs final transcripts
        if utterance.is_partial:
            # Accumulate partial transcript
            session.pending_transcript = utterance.text
            session.last_transcript_time = datetime.now()
        else:
            # Final transcript received
            full_text = utterance.text.strip()
            if len(full_text) >= self.min_utterance_length:
                session.pending_transcript = ""
                session.last_transcript_time = datetime.now()
                
                # Add to conversation history
                speaker = utterance.participant_name or "User"
                session.conversation_history.append({
                    "role": "user",
                    "speaker": speaker,
                    "content": full_text,
                    "timestamp": datetime.now().isoformat(),
                })
                
                # Process the utterance
                await self._process_utterance(bot_id, full_text, speaker)
    
    async def _handle_participant_event(self, bot_id: str, event: ParticipantEvent):
        """Handle participant events"""
        session = self.sessions.get(bot_id)
        if not session:
            return
        
        if event.event_type == "join":
            logger.info(f"👋 {event.participant_name} joined the meeting")
            # Optionally greet new participants
            # await self._send_greeting(bot_id, event.participant_name)
            
        elif event.event_type == "leave":
            logger.info(f"👋 {event.participant_name} left the meeting")
    
    # ========================================================================
    # Processing Pipeline
    # ========================================================================
    
    async def _process_utterance(self, bot_id: str, text: str, speaker: str):
        """Process an utterance through the LLM pipeline"""
        session = self.sessions.get(bot_id)
        if not session:
            return
        
        # Skip if already processing
        if session.is_processing:
            logger.debug(f"Already processing, skipping: {text[:50]}...")
            return
        
        session.is_processing = True
        session.state = SessionState.PROCESSING
        session.current_utterance = text
        
        logger.info(f"🎤 Processing utterance from {speaker}: {text}")
        
        try:
            # Generate response via LLM
            response = await self._generate_llm_response(session, text)
            
            if response:
                logger.info(f"🤖 Generated response: {response[:100]}...")
                
                # Add to history
                session.conversation_history.append({
                    "role": "assistant",
                    "content": response,
                    "timestamp": datetime.now().isoformat(),
                })
                
                # Convert to audio and send
                session.state = SessionState.RESPONDING
                await self._send_audio_response(bot_id, response)
                
        except Exception as e:
            logger.error(f"Error processing utterance: {e}", exc_info=True)
        finally:
            session.is_processing = False
            session.current_utterance = None
            session.last_response_time = datetime.now()
            session.state = SessionState.ACTIVE
    
    async def _generate_llm_response(self, session: VoiceSession, user_text: str) -> Optional[str]:
        """Generate a response using the LLM"""
        
        # Build context from conversation history
        system_prompt = """You are Jarvis, a helpful voice AI assistant in a Google Meet call. 
        Keep your responses concise and natural for voice conversation.
        Don't use complex formatting, emojis, or special characters.
        Be friendly and helpful."""
        
        # For now, use a simple approach - you can integrate your full LLM pipeline here
        if self.llm and self.llm_api_url:
            # Use your custom LLM API
            try:
                import aiohttp
                
                # Build chat history
                chat_history = []
                for msg in session.conversation_history[-10:]:  # Last 10 messages for context
                    if msg["role"] == "user":
                        chat_history.append({"role": "user", "content": msg["content"]})
                    elif msg["role"] == "assistant":
                        chat_history.append({"role": "assistant", "content": msg["content"]})
                
                payload = {
                    "userid": "recall_voice_agent",
                    "chat_history": chat_history[:-1],  # Exclude current message
                    "user_query": user_text,
                    "mode": "brief",
                    "source": "voice",
                }
                
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        self.llm_api_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        if response.status == 200:
                            # Handle streaming response
                            full_response = ""
                            async for chunk in response.content:
                                if chunk:
                                    full_response += chunk.decode("utf-8")
                            return full_response.strip()
                        else:
                            logger.error(f"LLM API error: {response.status}")
                            return None
                            
            except Exception as e:
                logger.error(f"LLM API call failed: {e}")
                return None
        else:
            # Fallback: Use OpenRouter or another LLM
            try:
                import aiohttp
                
                openrouter_key = os.getenv("OPENROUTER_API_KEY")
                if not openrouter_key:
                    logger.warning("No LLM configured, using echo response")
                    return f"I heard you say: {user_text}"
                
                messages = [
                    {"role": "system", "content": system_prompt},
                ]
                
                # Add conversation history
                for msg in session.conversation_history[-10:]:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })
                
                # Add current message
                messages.append({"role": "user", "content": user_text})
                
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        json={
                            "model": "google/gemini-2.0-flash-001",
                            "messages": messages,
                            "max_tokens": 200,
                        },
                        headers={
                            "Authorization": f"Bearer {openrouter_key}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://mochand.com",
                            "X-Title": "Recall Voice Agent",
                        },
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data["choices"][0]["message"]["content"]
                        else:
                            error = await response.text()
                            logger.error(f"OpenRouter error: {response.status} - {error}")
                            return None
                            
            except Exception as e:
                logger.error(f"OpenRouter call failed: {e}")
                return None
    
    async def _send_audio_response(self, bot_id: str, text: str):
        """Convert text to speech and send to meeting"""
        try:
            # Generate audio via Google TTS
            audio_b64 = await self._text_to_speech(text)
            
            if audio_b64:
                # Send to Recall.ai
                success = await self.recall_manager.output_audio(bot_id, audio_b64)
                if success:
                    logger.info(f"✅ Audio response sent successfully")
                else:
                    logger.error("Failed to send audio to meeting")
            else:
                logger.error("TTS failed, no audio generated")
                
        except Exception as e:
            logger.error(f"Error sending audio response: {e}", exc_info=True)
    
    async def _text_to_speech(self, text: str) -> Optional[str]:
        """Convert text to MP3 audio, return base64 encoded"""
        if not self.tts_client:
            logger.warning("Google TTS not available")
            return None
        
        try:
            # Configure the voice
            synthesis_input = texttospeech.SynthesisInput(text=text)
            
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-IN",  # Indian English
                ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
            )
            
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.0,
                pitch=0.0,
            )
            
            # Generate speech
            response = self.tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
            
            # Encode to base64
            audio_b64 = base64.b64encode(response.audio_content).decode("utf-8")
            return audio_b64
            
        except Exception as e:
            logger.error(f"TTS error: {e}", exc_info=True)
            return None
    
    # ========================================================================
    # Background Tasks
    # ========================================================================
    
    async def _silence_detection_loop(self):
        """Detect silence and trigger processing of accumulated transcript"""
        while True:
            try:
                await asyncio.sleep(0.5)  # Check every 500ms
                
                now = datetime.now()
                
                for bot_id, session in list(self.sessions.items()):
                    # Skip if not active or already processing
                    if session.state != SessionState.ACTIVE:
                        continue
                    if session.is_processing:
                        continue
                    
                    # Check for pending transcript with silence
                    if session.pending_transcript and session.last_transcript_time:
                        silence_duration = (now - session.last_transcript_time).total_seconds()
                        
                        if silence_duration >= self.silence_threshold:
                            text = session.pending_transcript.strip()
                            if len(text) >= self.min_utterance_length:
                                session.pending_transcript = ""
                                
                                # Process accumulated transcript
                                await self._process_utterance(bot_id, text, "User")
                                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in silence detection: {e}", exc_info=True)
    
    async def _handle_audio_chunk(self, bot_id: str, audio_data: dict):
        """
        Handle raw audio chunk from Recall.ai WebSocket.
        Processes audio through LiveKit bridge (STT → LLM → TTS).
        
        Args:
            bot_id: The bot's unique identifier
            audio_data: Audio data from WebSocket event
        """
        session = self.sessions.get(bot_id)
        if not session:
            logger.warning(f"No session found for bot {bot_id}")
            return
        
        # Process audio through the LiveKit bridge
        await self.audio_bridge.process_audio_chunk(bot_id, audio_data)


# ============================================================================
# Main Entry Point
# ============================================================================

async def main():
    """Main entry point for running the session coordinator"""
    load_dotenv(".env.local")
    
    # Get configuration from environment
    recall_api_key = os.getenv("RECALL_API_KEY", "template")
    websocket_port = int(os.getenv("WEBSOCKET_PORT", "8765"))
    
    # Initialize coordinator
    coordinator = SessionCoordinator(
        recall_api_key=recall_api_key,
        websocket_port=websocket_port,
        google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE"),
        llm_api_url=os.getenv("AGENT_API_URL"),
    )
    
    # Start services
    await coordinator.start()
    
    # Example: Join a meeting
    meeting_url = os.getenv("TEST_MEETING_URL", "https://meet.google.com/bor-hhdy-gfv")
    bot_name = os.getenv("BOT_NAME", "Jarvis")
    
    if meeting_url:
        try:
            session = await coordinator.join_meeting(meeting_url, bot_name)
            logger.info(f"Joined meeting with bot: {session.bot_id}")
        except Exception as e:
            logger.error(f"Failed to join meeting: {e}")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await coordinator.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
