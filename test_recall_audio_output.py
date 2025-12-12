"""
Test Recall.ai's audio output capability.

This test will:
1. Create a bot and join Google Meet
2. Generate TTS audio using Google Cloud TTS (same as agent.py)
3. Send the audio to Google Meet via Recall.ai's output_audio API
4. Verify if the bot actually speaks in the meeting

This tests the CRITICAL capability: Can Recall.ai output audio to Google Meet?
"""

import asyncio
import aiohttp
import os
import base64
from dotenv import load_dotenv
from google.cloud import texttospeech
import json

load_dotenv('.env.local')

RECALL_API_KEY = os.getenv('RECALL_API_KEY')
RECALL_BASE_URL = "https://us-west-2.recall.ai"
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')


async def create_test_bot(meeting_url: str) -> str:
    """Create a bot and join Google Meet."""
    
    headers = {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Minimal config - we just need the bot to join
    payload = {
        "meeting_url": meeting_url,
        "bot_name": "Audio Test Bot",
        "recording_config": {
            "participant_events": {},
            "realtime_endpoints": []  # No WebSocket needed for this test
        }
        # Note: We don't need automatic_audio_output config
        # The output_audio API works without it
    }
    
    print("\n" + "="*80)
    print("TEST: Recall.ai Audio Output Capability")
    print("="*80)
    print(f"\n📞 Creating bot and joining meeting...")
    print(f"Meeting: {meeting_url}")
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{RECALL_BASE_URL}/api/v1/bot/",
            headers=headers,
            json=payload
        ) as response:
            
            if response.status == 201:
                data = await response.json()
                bot_id = data['id']
                print(f"✅ Bot created: {bot_id}")
                return bot_id
            else:
                error = await response.text()
                print(f"❌ Failed to create bot: {response.status}")
                print(error)
                return None


async def wait_for_bot_in_meeting(bot_id: str, timeout: int = 60):
    """Wait for bot to actually join the meeting."""
    
    headers = {"Authorization": f"Token {RECALL_API_KEY}"}
    
    print(f"\n⏳ Waiting for bot to join meeting (timeout: {timeout}s)...")
    print(f"⚠️  IMPORTANT: Someone must be in the meeting for the bot to join!")
    print(f"   Please join: https://meet.google.com/bor-hhdy-gfv")
    print(f"\n   Checking bot status...")
    
    start_time = asyncio.get_event_loop().time()
    
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(
                f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
                headers=headers
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    
                    if 'status_changes' in data and len(data['status_changes']) > 0:
                        latest_status = data['status_changes'][-1]
                        status_code = latest_status.get('code')
                        
                        print(f"   Bot status: {status_code}")
                        
                        if status_code in ['in_call_not_recording', 'in_call_recording']:
                            print(f"✅ Bot is in the meeting!")
                            return True
                        elif status_code == 'in_waiting_room':
                            print(f"⏳ Bot is in waiting room - please admit it to the meeting")
                        elif status_code in ['fatal', 'done']:
                            print(f"❌ Bot failed to join: {status_code}")
                            return False
            
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                print(f"❌ Timeout waiting for bot to join")
                return False
            
            await asyncio.sleep(2)


def generate_test_audio(text: str) -> bytes:
    """Generate TTS audio using Google Cloud TTS (same as agent.py uses)."""
    
    print(f"\n🎙️ Generating TTS audio for: '{text}'")
    
    # Set Google credentials - use the file in the project root
    credentials_path = "google_credentials_file.json"
    if os.path.exists(credentials_path):
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.abspath(credentials_path)
    
    # Initialize Google Cloud TTS client
    client = texttospeech.TextToSpeechClient()
    
    # Configure synthesis input
    synthesis_input = texttospeech.SynthesisInput(text=text)
    
    # Configure voice (same as agent.py)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name="en-US-Neural2-J",  # Male voice
        ssml_gender=texttospeech.SsmlVoiceGender.MALE
    )
    
    # Configure audio output as MP3 (required by Recall.ai output_audio API)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        sample_rate_hertz=24000  # Standard MP3 sample rate
    )
    
    # Perform the text-to-speech request
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )
    
    print(f"✅ Generated {len(response.audio_content)} bytes of MP3 audio")
    
    return response.audio_content


async def send_audio_to_bot(bot_id: str, audio_data: bytes) -> bool:
    """Send audio to Recall.ai bot via output_audio API."""
    
    headers = {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Encode audio as base64
    b64_audio = base64.b64encode(audio_data).decode('utf-8')
    
    payload = {
        "b64_data": b64_audio,
        "kind": "mp3"  # Recall.ai expects MP3 format
    }
    
    print(f"\n📤 Sending {len(audio_data)} bytes of audio to bot {bot_id}...")
    print(f"   Base64 size: {len(b64_audio)} chars")
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/output_audio",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            
            response_text = await response.text()
            
            if response.status == 200:
                print(f"✅ Audio sent successfully!")
                print(f"   Response: {response_text}")
                return True
            else:
                print(f"❌ Failed to send audio: {response.status}")
                print(f"   Error: {response_text}")
                return False


async def delete_bot(bot_id: str):
    """Delete the test bot."""
    
    headers = {"Authorization": f"Token {RECALL_API_KEY}"}
    
    print(f"\n🗑️ Deleting bot {bot_id}...")
    
    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
            headers=headers
        ) as response:
            
            if response.status == 204:
                print(f"✅ Bot deleted successfully")
            else:
                print(f"⚠️ Failed to delete bot: {response.status}")


async def main():
    """Run the audio output test."""
    
    # Configuration
    MEETING_URL = "https://meet.google.com/vzo-atnj-foh"
    TEST_MESSAGE = "Hello! This is a test message from the Recall AI bot. Can you hear me in Google Meet?"
    
    bot_id = None
    
    try:
        # Step 1: Create bot and join meeting
        bot_id = await create_test_bot(MEETING_URL)
        
        if not bot_id:
            print("\n❌ TEST FAILED: Could not create bot")
            return
        
        # Step 2: Wait for bot to join the meeting
        if not await wait_for_bot_in_meeting(bot_id):
            print("\n❌ TEST FAILED: Bot did not join meeting")
            return
        
        # Step 3: Generate test audio
        audio_data = generate_test_audio(TEST_MESSAGE)
        
        # Step 4: Send audio to bot
        print("\n" + "="*80)
        print("SENDING AUDIO TO GOOGLE MEET")
        print("="*80)
        print("\n⚠️ IMPORTANT: Join the Google Meet now and listen for the bot to speak!")
        print(f"   Meeting: {MEETING_URL}")
        print(f"   Message: '{TEST_MESSAGE}'")
        print("\nWaiting 5 seconds for you to join the meeting...")
        
        await asyncio.sleep(5)
        
        success = await send_audio_to_bot(bot_id, audio_data)
        
        # Step 5: Wait a bit for the audio to play
        if success:
            print("\n⏳ Audio sent! Waiting 10 seconds for playback...")
            await asyncio.sleep(10)
        
        # Results
        print("\n" + "="*80)
        print("TEST RESULTS")
        print("="*80)
        
        if success:
            print("\n✅ Audio was sent successfully to Recall.ai")
            print("\n🎯 NEXT STEPS:")
            print("   1. Did you hear the bot speak in Google Meet?")
            print("   2. If YES → Recall.ai audio output WORKS!")
            print("   3. If NO → Recall.ai audio output does NOT work in your plan")
            print("\n📝 If the bot spoke, we can proceed with the full integration:")
            print("   Google Meet → Recall.ai → LiveKit STT → LLM → LiveKit TTS → Recall.ai → Google Meet")
        else:
            print("\n❌ Failed to send audio to Recall.ai")
            print("   This means the output_audio API is not working")
        
    except Exception as e:
        print(f"\n❌ TEST ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        if bot_id:
            await delete_bot(bot_id)
        
        print("\n" + "="*80)
        print("Test complete!")
        print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
