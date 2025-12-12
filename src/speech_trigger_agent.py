"""
Speech-Triggered Voice Agent

Since Recall.ai free tier doesn't provide audio/transcripts, this uses
speech detection events to trigger the LLM and respond in Google Meet.

Flow:
1. Recall.ai detects participant speech (speech_on event)
2. Query LLM directly (Google Gemini via OpenRouter)
3. Generate TTS (Google Cloud TTS → MP3)
4. Send MP3 back to Google Meet via Recall.ai output_audio API

This proves the architecture works even without real audio input.
For real conversations, you need paid Deepgram transcription.
"""

import asyncio
import logging
from typing import Optional
from google.cloud import texttospeech
import aiohttp
import json
import os
import base64
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv('.env.local')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('speech-trigger-agent')

# Configuration
RECALL_API_KEY = os.getenv('RECALL_API_KEY')
RECALL_BASE_URL = "https://us-west-2.recall.ai"
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')

# Set Google credentials
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.abspath('google_credentials_file.json')


class SpeechTriggerAgent:
    """Agent that responds to speech detection events."""
    
    def __init__(self):
        self.tts_client = texttospeech.TextToSpeechClient()
        self.response_count = 0
        self.last_response_time = {}  # Track last response time per bot
        self.min_response_interval = 10  # Minimum seconds between responses
        
        # Initialize OpenAI client for OpenRouter
        self.llm_client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1"
        )
        
        # Preset responses to cycle through (fallback)
        self.preset_responses = [
            "Hello! I detected that someone is speaking in the meeting.",
            "I can hear activity in the meeting. This is a test of the Recall AI audio output.",
            "Speech detected! I'm responding through the Recall AI output audio API.",
            "This demonstrates that the bot can speak in Google Meet using LiveKit's text to speech.",
        ]
    
    async def get_llm_response(self, query: str) -> str:
        """Get LLM response for a query using Google Gemini via OpenRouter."""
        
        logger.info(f"💬 Sending query to LLM: {query}")
        
        try:
            response = await self.llm_client.chat.completions.create(
                model="google/gemini-2.0-flash-001:online",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful voice assistant in a Google Meet call. Keep responses brief and conversational (1-2 sentences max)."
                    },
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                temperature=0.7,
                max_tokens=100
            )
            
            llm_response = response.choices[0].message.content
            logger.info(f"🤖 LLM response: {llm_response}")
            return llm_response
            
        except Exception as e:
            logger.error(f"❌ Error calling LLM: {e}")
            return self._get_preset_response()
    
    def _get_preset_response(self) -> str:
        """Get a preset response (fallback when LLM unavailable)."""
        response = self.preset_responses[self.response_count % len(self.preset_responses)]
        self.response_count += 1
        return response
    
    def generate_tts_audio(self, text: str) -> bytes:
        """Generate MP3 audio from text using Google Cloud TTS."""
        
        logger.info(f"Generating TTS for: '{text[:50]}...'")
        
        # Configure synthesis
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        # Configure voice
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Neural2-J",  # Male voice
            ssml_gender=texttospeech.SsmlVoiceGender.MALE
        )
        
        # Configure audio output as MP3
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            sample_rate_hertz=24000
        )
        
        # Perform TTS
        response = self.tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        logger.info(f"Generated {len(response.audio_content)} bytes of MP3 audio")
        return response.audio_content
    
    async def send_audio_to_bot(self, bot_id: str, audio_data: bytes) -> bool:
        """Send audio to Recall.ai bot via output_audio API."""
        
        headers = {
            "Authorization": f"Token {RECALL_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Encode audio as base64
        b64_audio = base64.b64encode(audio_data).decode('utf-8')
        
        payload = {
            "b64_data": b64_audio,
            "kind": "mp3"
        }
        
        logger.info(f"Sending {len(audio_data)} bytes of audio to bot {bot_id}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/output_audio",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                
                if response.status == 200:
                    logger.info("✅ Audio sent successfully to Google Meet")
                    return True
                else:
                    error = await response.text()
                    logger.error(f"❌ Failed to send audio: {response.status} - {error}")
                    return False
    
    async def handle_speech_event(self, bot_id: str, is_speech_on: bool):
        """Handle speech detection event."""
        
        if not is_speech_on:
            return  # Only respond to speech_on events
        
        # Rate limiting: Don't respond too frequently
        import time
        current_time = time.time()
        last_time = self.last_response_time.get(bot_id, 0)
        
        if current_time - last_time < self.min_response_interval:
            logger.info(f"⏭️ Skipping response (too soon, {current_time - last_time:.1f}s since last)")
            return
        
        self.last_response_time[bot_id] = current_time
        
        logger.info(f"🎤 Speech detected for bot {bot_id}")
        
        # Generate a response
        # In a real implementation, you'd get the actual transcript
        # For now, we use a preset or trigger the LLM with a test query
        test_query = "Someone is speaking in the meeting. Provide a friendly acknowledgment."
        
        # Get LLM response
        response_text = await self.get_llm_response(test_query)
        
        # Generate TTS audio
        audio_data = self.generate_tts_audio(response_text)
        
        # Send to Google Meet
        success = await self.send_audio_to_bot(bot_id, audio_data)
        
        if success:
            logger.info(f"✅ Bot spoke in Google Meet: '{response_text}'")
        else:
            logger.error(f"❌ Failed to make bot speak")


# Global agent instance
agent = SpeechTriggerAgent()


async def on_speech_detected(bot_id: str):
    """Callback when speech is detected in the meeting."""
    await agent.handle_speech_event(bot_id, is_speech_on=True)


if __name__ == "__main__":
    # Test the agent
    async def test():
        await on_speech_detected("test-bot-id")
    
    asyncio.run(test())
