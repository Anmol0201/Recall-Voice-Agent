"""Check the actual configuration of the running bot."""
import asyncio
import aiohttp
import os
import json
from dotenv import load_dotenv

load_dotenv('.env.local')

RECALL_API_KEY = os.getenv('RECALL_API_KEY')
RECALL_BASE_URL = "https://us-west-2.recall.ai"

async def check_bot(bot_id: str):
    headers = {"Authorization": f"Token {RECALL_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{RECALL_BASE_URL}/api/v1/bot/{bot_id}/",
            headers=headers
        ) as response:
            if response.status == 200:
                data = await response.json()
                
                print("\n" + "="*80)
                print(f"BOT CONFIGURATION - {bot_id}")
                print("="*80)
                
                print(f"\n📊 Status: {data.get('status_changes', [{}])[-1].get('code', 'unknown')}")
                
                config = data.get('recording_config', {})
                
                print(f"\n🎙️ Recording Configuration:")
                print(json.dumps(config, indent=2))
                
                # Check if audio_mixed_raw is actually enabled
                if 'audio_mixed_raw' in config:
                    audio_config = config['audio_mixed_raw']
                    if audio_config:
                        print(f"\n✅ audio_mixed_raw is configured:")
                        print(json.dumps(audio_config, indent=2))
                    else:
                        print(f"\n⚠️ audio_mixed_raw is EMPTY: {audio_config}")
                
                # Check transcript provider
                if 'transcript' in config:
                    transcript_config = config['transcript']
                    print(f"\n📝 Transcript configuration:")
                    print(json.dumps(transcript_config, indent=2))
                
                # Check realtime endpoints
                if 'realtime_endpoints' in config:
                    print(f"\n🔌 Realtime Endpoints:")
                    for endpoint in config['realtime_endpoints']:
                        print(f"  Type: {endpoint.get('type')}")
                        print(f"  URL: {endpoint.get('url')}")
                        print(f"  Events: {endpoint.get('events')}")
            else:
                print(f"Error: {response.status}")
                print(await response.text())

if __name__ == "__main__":
    # Use the bot ID from the logs
    asyncio.run(check_bot("e9899070-a3c5-4514-a57b-66151d58d199"))
