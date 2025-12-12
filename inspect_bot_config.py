"""
Simple script to check what's in the actual bot configuration after creation.
This will tell us if audio_mixed_raw is properly enabled.
"""

import asyncio
import aiohttp
import os
from dotenv import load_dotenv
import json

load_dotenv('.env.local')

RECALL_API_KEY = os.getenv('RECALL_API_KEY')
RECALL_BASE_URL = "https://us-west-2.recall.ai"

async def create_and_inspect_bot():
    """Create a bot with raw audio and inspect the actual configuration."""
    
    headers = {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Minimal config requesting ONLY raw audio (no transcription)
    payload = {
        "meeting_url": "https://meet.google.com/bor-hhdy-gfv",
        "bot_name": "Inspection Test",
        "recording_config": {
            "audio_mixed_raw": {
                "sample_rate": 48000,  # Try specifying sample rate
                "encoding": "pcm_s16le"  # Try specifying encoding
            },
            "realtime_endpoints": [{
                "type": "websocket",
                "url": "wss://twelve-reproduction-continental-logged.trycloudflare.com",
                "events": ["audio_mixed_raw.data", "participant_events.join"]
            }]
        }
    }
    
    print("\n📤 Creating bot with explicit audio config...")
    print(json.dumps(payload, indent=2))
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{RECALL_BASE_URL}/api/v1/bot/",
            headers=headers,
            json=payload
        ) as response:
            
            if response.status == 201:
                data = await response.json()
                bot_id = data['id']
                
                print(f"\n✅ Bot created: {bot_id}")
                print(f"\n📊 Returned recording_config:")
                print(json.dumps(data.get('recording_config', {}), indent=2))
                
                # Wait a moment then check bot status
                await asyncio.sleep(5)
                
                async with session.get(
                    f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
                    headers=headers
                ) as status_response:
                    
                    if status_response.status == 200:
                        status_data = await status_response.json()
                        
                        print(f"\n📊 Bot Status:")
                        if 'status_changes' in status_data and len(status_data['status_changes']) > 0:
                            latest_status = status_data['status_changes'][-1]
                            print(f"  Code: {latest_status.get('code')}")
                            print(f"  Message: {latest_status.get('message')}")
                        
                        print(f"\n🎙️ Actual Recording Config:")
                        config = status_data.get('recording_config', {})
                        
                        if 'audio_mixed_raw' in config:
                            print(f"  audio_mixed_raw: {json.dumps(config['audio_mixed_raw'], indent=4)}")
                        else:
                            print(f"  ❌ audio_mixed_raw not in config!")
                        
                        if 'realtime_endpoints' in config:
                            print(f"\n  realtime_endpoints:")
                            for endpoint in config['realtime_endpoints']:
                                print(f"    - Type: {endpoint.get('type')}")
                                print(f"      Events: {endpoint.get('events')}")
                
                # Clean up
                print(f"\n🗑️ Deleting bot...")
                await session.delete(
                    f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
                    headers=headers
                )
                
            else:
                error = await response.text()
                print(f"\n❌ Failed: {response.status}")
                print(error)

if __name__ == "__main__":
    asyncio.run(create_and_inspect_bot())
