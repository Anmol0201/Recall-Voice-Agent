"""
Test script to verify what audio/transcript events Recall.ai actually sends.
This will help determine if raw audio streaming is available in our plan.
"""

import asyncio
import aiohttp
import json
import os
from dotenv import load_dotenv

load_dotenv('.env.local')

RECALL_API_KEY = os.getenv('RECALL_API_KEY')
RECALL_BASE_URL = "https://us-west-2.recall.ai"

# Test different configurations to see what actually works
CONFIGS_TO_TEST = [
    {
        "name": "Raw Audio Only (No Transcription)",
        "config": {
            "audio_mixed_raw": {"enabled": True},
            "realtime_endpoints": [{
                "type": "websocket",
                "url": "wss://optimize-tide-memo-progress.trycloudflare.com",
                "events": ["audio_mixed_raw.data", "participant_events.join", "participant_events.leave"]
            }]
        }
    },
    {
        "name": "With Deepgram Transcription (No API Key - Should Fail)",
        "config": {
            "transcript": {
                "provider": {
                    "deepgram_streaming": {
                        "tier": "nova",
                        "model": "general",
                        "language": "en-US"
                    }
                }
            },
            "audio_mixed_raw": {"enabled": True},
            "realtime_endpoints": [{
                "type": "websocket",
                "url": "wss://optimize-tide-memo-progress.trycloudflare.com",
                "events": ["audio_mixed_raw.data", "transcript.data", "participant_events.join"]
            }]
        }
    },
    {
        "name": "RecallAI Transcription Provider",
        "config": {
            "transcript": {
                "provider": {
                    "recallai_streaming": {}
                }
            },
            "audio_mixed_raw": {"enabled": True},
            "realtime_endpoints": [{
                "type": "websocket",
                "url": "wss://optimize-tide-memo-progress.trycloudflare.com",
                "events": ["audio_mixed_raw.data", "transcript.data", "participant_events.join"]
            }]
        }
    }
]


async def test_bot_creation(config_name: str, recording_config: dict):
    """Test bot creation with a specific configuration and check the API response."""
    
    print(f"\n{'='*80}")
    print(f"Testing: {config_name}")
    print(f"{'='*80}")
    
    # Don't actually create a bot, just validate the config with API
    headers = {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "meeting_url": "https://meet.google.com/bor-hhdy-gfv",  # Use real meeting URL
        "bot_name": "Config Test Bot",
        "recording_config": recording_config
    }
    
    print(f"\n📤 Sending bot creation request...")
    print(f"Recording config: {json.dumps(recording_config, indent=2)}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{RECALL_BASE_URL}/api/v1/bot/",
                headers=headers,
                json=payload
            ) as response:
                
                status = response.status
                response_text = await response.text()
                
                print(f"\n📥 Response Status: {status}")
                
                if status == 200 or status == 201:
                    print("✅ SUCCESS - Bot creation accepted!")
                    data = json.loads(response_text)
                    print(f"Bot ID: {data.get('id')}")
                    print(f"Status URL: {data.get('status_url')}")
                    
                    # Check what features are actually enabled
                    if 'recording_config' in data:
                        print(f"\n🔍 Actual Recording Config Returned:")
                        print(json.dumps(data['recording_config'], indent=2))
                    
                    return data.get('id')
                    
                elif status == 400:
                    print("❌ FAILED - Bad Request (Invalid configuration)")
                    try:
                        error_data = json.loads(response_text)
                        print(f"Error details: {json.dumps(error_data, indent=2)}")
                    except:
                        print(f"Error: {response_text}")
                    
                elif status == 402:
                    print("💰 PAYMENT REQUIRED - This feature needs a paid plan!")
                    print(f"Response: {response_text}")
                    
                else:
                    print(f"❓ Unexpected status code")
                    print(f"Response: {response_text}")
                    
        except Exception as e:
            print(f"❌ Exception occurred: {str(e)}")
            
    return None


async def check_bot_status(bot_id: str):
    """Check the actual status and configuration of a created bot."""
    
    if not bot_id:
        return
        
    headers = {
        "Authorization": f"Token {RECALL_API_KEY}"
    }
    
    print(f"\n{'='*80}")
    print(f"Checking Bot Status: {bot_id}")
    print(f"{'='*80}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
                headers=headers
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    
                    print(f"\n📊 Bot Status: {data.get('status_changes', [{}])[-1].get('code', 'unknown')}")
                    
                    # Check what recording features are actually active
                    if 'recording_config' in data:
                        config = data['recording_config']
                        print(f"\n🎙️ Recording Configuration:")
                        
                        if 'audio_mixed_raw' in config:
                            print(f"  - Audio Mixed Raw: {config['audio_mixed_raw']}")
                        
                        if 'transcript' in config:
                            print(f"  - Transcript: {config['transcript']}")
                        
                        if 'realtime_endpoints' in config:
                            print(f"  - Realtime Endpoints: {len(config['realtime_endpoints'])} configured")
                            for endpoint in config['realtime_endpoints']:
                                print(f"    - Type: {endpoint.get('type')}")
                                print(f"    - Events: {endpoint.get('events')}")
                    
                    # Delete the test bot to avoid charges
                    print(f"\n🗑️ Deleting test bot...")
                    async with session.delete(
                        f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
                        headers=headers
                    ) as delete_response:
                        if delete_response.status == 204:
                            print("✅ Test bot deleted successfully")
                        
        except Exception as e:
            print(f"❌ Error checking bot status: {str(e)}")


async def main():
    """Run all configuration tests."""
    
    print("\n" + "="*80)
    print("RECALL.AI AUDIO STREAMING CAPABILITY TEST")
    print("="*80)
    print("\nThis will test different configurations to see:")
    print("1. Which configs are accepted by the API")
    print("2. Whether raw audio streaming requires transcription")
    print("3. What error messages we get for paid features")
    
    # Test each configuration
    for test_case in CONFIGS_TO_TEST:
        bot_id = await test_bot_creation(
            test_case["name"],
            test_case["config"]
        )
        
        if bot_id:
            await check_bot_status(bot_id)
        
        # Wait between tests
        await asyncio.sleep(2)
    
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print("\n🎯 Key Questions to Answer:")
    print("1. Does raw audio work WITHOUT transcription provider?")
    print("2. Do we get a 402 (Payment Required) error?")
    print("3. What's the actual recording_config returned by the API?")
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
