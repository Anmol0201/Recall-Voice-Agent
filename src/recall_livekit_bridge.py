"""
Recall.ai ↔ LiveKit Audio Bridge
Bridges audio between Recall.ai bot and LiveKit voice agent for real-time conversation.
"""
import asyncio
import base64
import logging
import io
from typing import Optional
from dataclasses import dataclass
from livekit import rtc
from livekit.agents import stt, tts
from livekit.plugins import openai, google
from llm import AgentLLM
import os
from pydub import AudioSegment

logger = logging.getLogger("recall-livekit-bridge")
logger.setLevel(logging.INFO)


@dataclass
class AudioChunk:
    """Audio chunk from Recall.ai"""
    data: bytes
    timestamp: float
    speaker_id: Optional[str] = None


class RecallLiveKitBridge:
    """
    Bridges audio between Recall.ai WebSocket and LiveKit voice agent.
    
    Flow:
    1. Receive raw audio from Recall.ai WebSocket (audio_mixed_raw.data)
    2. Convert to format suitable for OpenAI STT
    3. Get transcript from STT
    4. Send transcript to LLM (via existing AgentLLM)
    5. Get response text from LLM
    6. Convert to speech via Google TTS
    7. Send MP3 audio back to Recall.ai via output_audio API
    """
    
    def __init__(
        self,
        bot_manager,
        stt_provider: Optional[stt.STT] = None,
        tts_provider: Optional[tts.TTS] = None,
        llm_api_url: str = "http://localhost:8020/api/stream",
    ):
        self.bot_manager = bot_manager
        
        # Initialize STT (Speech-to-Text)
        self.stt = stt_provider or openai.STT()
        logger.info(f"Initialized STT: {self.stt.__class__.__name__}")
        
        # Initialize TTS (Text-to-Speech)
        google_creds = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials_file.json")
        self.tts = tts_provider or google.TTS(gender="female", credentials_file=google_creds)
        logger.info(f"Initialized TTS: {self.tts.__class__.__name__}")
        
        # Initialize LLM
        self.llm = AgentLLM(
            api_url=llm_api_url,
            userid="recall_voice_agent",
            mode="brief",
            source="voice",
            timeout=60,
        )
        logger.info(f"Initialized LLM: {llm_api_url}")
        
        # Audio buffer for chunking
        self.audio_buffer = bytearray()
        self.conversation_history = []
        
        # Processing flags
        self.is_processing = False
        self.last_response_time = 0
        
    async def process_audio_chunk(self, bot_id: str, audio_data: dict):
        """
        Process incoming audio chunk from Recall.ai.
        
        Args:
            bot_id: The bot's unique identifier
            audio_data: Audio data from WebSocket event
        """
        if self.is_processing:
            logger.debug("Already processing audio, skipping chunk")
            return
            
        try:
            # Extract audio from base64
            audio_b64 = audio_data.get("data", {}).get("b64_data", "")
            if not audio_b64:
                logger.warning("No audio data in chunk")
                return
            
            audio_bytes = base64.b64decode(audio_b64)
            logger.debug(f"Received audio chunk: {len(audio_bytes)} bytes")
            
            # Add to buffer
            self.audio_buffer.extend(audio_bytes)
            
            # Process when we have enough audio (e.g., 2 seconds at 16kHz)
            # Assuming raw PCM 16-bit mono at 16kHz = 32000 bytes per second
            min_chunk_size = 64000  # ~2 seconds
            
            if len(self.audio_buffer) >= min_chunk_size:
                await self._process_buffered_audio(bot_id)
                
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}", exc_info=True)
    
    async def _process_buffered_audio(self, bot_id: str):
        """Process accumulated audio buffer through STT → LLM → TTS → Recall.ai"""
        if not self.audio_buffer:
            return
            
        self.is_processing = True
        
        try:
            # Get audio bytes
            audio_bytes = bytes(self.audio_buffer)
            self.audio_buffer.clear()
            
            logger.info(f"Processing {len(audio_bytes)} bytes of audio")
            
            # Step 1: Convert audio to text using STT
            transcript = await self._transcribe_audio(audio_bytes)
            
            if not transcript or len(transcript.strip()) < 3:
                logger.debug("No meaningful transcript, skipping")
                self.is_processing = False
                return
            
            logger.info(f"📝 User said: {transcript}")
            
            # Step 2: Get LLM response
            response_text = await self._get_llm_response(transcript)
            
            if not response_text:
                logger.warning("No LLM response")
                self.is_processing = False
                return
            
            logger.info(f"🤖 Agent response: {response_text}")
            
            # Step 3: Convert response to speech
            audio_mp3 = await self._synthesize_speech(response_text)
            
            if not audio_mp3:
                logger.error("Failed to synthesize speech")
                self.is_processing = False
                return
            
            # Step 4: Send audio to Recall.ai bot
            await self._send_audio_to_bot(bot_id, audio_mp3)
            
            logger.info("✅ Completed audio processing pipeline")
            
        except Exception as e:
            logger.error(f"Error in audio processing pipeline: {e}", exc_info=True)
        finally:
            self.is_processing = False
    
    async def _transcribe_audio(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio using OpenAI STT.
        
        Args:
            audio_bytes: Raw audio bytes (PCM format expected)
            
        Returns:
            Transcribed text
        """
        try:
            # Convert raw PCM to WAV format for STT
            # Assuming 16-bit PCM mono at 16kHz (Recall.ai default)
            audio_segment = AudioSegment(
                data=audio_bytes,
                sample_width=2,  # 16-bit = 2 bytes
                frame_rate=16000,  # 16kHz
                channels=1,  # Mono
            )
            
            # Export to WAV in memory
            wav_buffer = io.BytesIO()
            audio_segment.export(wav_buffer, format="wav")
            wav_bytes = wav_buffer.getvalue()
            
            # Create audio frame for STT
            # Note: This is a simplified approach. In production, you'd use LiveKit's audio frame format
            # For now, we'll use OpenAI's Whisper API directly
            
            import aiohttp
            api_key = os.getenv("OPENAI_API_KEY")
            
            if not api_key:
                logger.error("OPENAI_API_KEY not set")
                return ""
            
            # Use OpenAI Whisper API
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('file', wav_bytes, filename='audio.wav', content_type='audio/wav')
                data.add_field('model', 'whisper-1')
                
                async with session.post(
                    'https://api.openai.com/v1/audio/transcriptions',
                    headers={'Authorization': f'Bearer {api_key}'},
                    data=data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get('text', '')
                    else:
                        error_text = await response.text()
                        logger.error(f"STT API error: {response.status} - {error_text}")
                        return ""
                        
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}", exc_info=True)
            return ""
    
    async def _get_llm_response(self, user_message: str) -> str:
        """
        Get LLM response for user message.
        
        Args:
            user_message: User's transcribed message
            
        Returns:
            LLM response text
        """
        try:
            # Add to conversation history
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            
            # Prepare messages for LLM
            messages = [
                {
                    "role": "system",
                    "content": """You are Jarvis, a helpful voice AI assistant in a Google Meet conversation. 
                    The user is talking to you via voice, so keep responses natural and conversational.
                    Be concise, friendly, and helpful. Avoid complex formatting or symbols."""
                }
            ] + self.conversation_history[-10:]  # Keep last 10 messages for context
            
            # Call LLM API
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                payload = {
                    "userid": "recall_voice_agent",
                    "message": user_message,
                    "mode": "brief",
                    "source": "voice"
                }
                
                async with session.post(
                    self.llm.api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        # Stream response
                        response_text = ""
                        async for line in response.content:
                            line_text = line.decode('utf-8').strip()
                            if line_text.startswith('data: '):
                                data_str = line_text[6:]
                                if data_str == '[DONE]':
                                    break
                                try:
                                    import json
                                    data = json.loads(data_str)
                                    if 'content' in data:
                                        response_text += data['content']
                                except:
                                    pass
                        
                        # Add to history
                        self.conversation_history.append({
                            "role": "assistant",
                            "content": response_text
                        })
                        
                        return response_text
                    else:
                        logger.error(f"LLM API error: {response.status}")
                        return "I'm sorry, I'm having trouble processing that right now."
                        
        except Exception as e:
            logger.error(f"Error getting LLM response: {e}", exc_info=True)
            return "I apologize, but I encountered an error."
    
    async def _synthesize_speech(self, text: str) -> bytes:
        """
        Convert text to speech using Google TTS.
        
        Args:
            text: Text to synthesize
            
        Returns:
            MP3 audio bytes
        """
        try:
            # Use Google Cloud TTS API
            from google.cloud import texttospeech
            
            client = texttospeech.TextToSpeechClient.from_service_account_file(
                os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials_file.json")
            )
            
            synthesis_input = texttospeech.SynthesisInput(text=text)
            
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
            )
            
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.0,
                pitch=0.0,
            )
            
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            
            return response.audio_content
            
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}", exc_info=True)
            return b""
    
    async def _send_audio_to_bot(self, bot_id: str, audio_mp3: bytes):
        """
        Send MP3 audio to Recall.ai bot via output_audio API.
        
        Args:
            bot_id: The bot's unique identifier
            audio_mp3: MP3 audio bytes
        """
        try:
            # Convert MP3 to base64
            audio_b64 = base64.b64encode(audio_mp3).decode('utf-8')
            
            # Send via bot manager
            success = await self.bot_manager.output_audio(bot_id, audio_b64)
            
            if success:
                logger.info(f"✅ Sent {len(audio_mp3)} bytes of audio to bot {bot_id}")
            else:
                logger.error(f"❌ Failed to send audio to bot {bot_id}")
                
        except Exception as e:
            logger.error(f"Error sending audio to bot: {e}", exc_info=True)
    
    def clear_history(self):
        """Clear conversation history"""
        self.conversation_history.clear()
        logger.info("Cleared conversation history")
