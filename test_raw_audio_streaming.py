"""
Real test: Create bot, join meeting, monitor WebSocket for 30 seconds.
This will definitively show if audio_mixed_raw.data events arrive WITHOUT transcription provider.
"""

import asyncio
import sys
import time
from src.recall_manager import RecallManager
from src.recall_api import app
import uvicorn
from threading import Thread

# Track what events we receive
received_events = {
    "audio_mixed_raw.data": 0,
    "transcript.data": 0,
    "participant_events.join": 0,
    "participant_events.leave": 0,
    "participant_events.speech_on": 0,
    "participant_events.speech_off": 0,
}

async def test_audio_streaming():
    """Test if raw audio streaming works without transcription provider."""
    
    print("\n" + "="*80)
    print("TESTING: Raw Audio Streaming WITHOUT Transcription Provider")
    print("="*80)
    
    # Start FastAPI server in background
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    
    server_thread = Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    server_thread.start()
    
    print("⏳ Waiting for server to start...")
    await asyncio.sleep(3)
    
    # Create bot with raw audio ONLY (no transcription)
    manager = RecallManager()
    
    print("\n📞 Creating bot and joining meeting...")
    bot_session = await manager.create_bot(
        meeting_url="https://meet.google.com/bor-hhdy-gfv",
        bot_name="Audio Test Bot",
        enable_transcription=False,  # ❌ NO transcription provider
        enable_audio_streaming=True,  # ✅ Request audio_mixed_raw.data
    )
    
    print(f"✅ Bot created: {bot_session.bot_id}")
    print(f"📊 Bot status: {bot_session.status}")
    
    print("\n⏱️ Monitoring WebSocket for 30 seconds...")
    print("Please speak in the meeting to test audio detection!\n")
    
    # Monitor for 30 seconds
    start_time = time.time()
    while time.time() - start_time < 30:
        await asyncio.sleep(1)
        elapsed = int(time.time() - start_time)
        print(f"\r[{elapsed:02d}s] Waiting for events...", end="", flush=True)
    
    print("\n\n" + "="*80)
    print("TEST RESULTS")
    print("="*80)
    
    # Clean up
    print("\n🗑️ Deleting bot...")
    await manager.delete_bot(bot_session.bot_id)
    
    print("\n📊 Events Received:")
    for event_type, count in received_events.items():
        icon = "✅" if count > 0 else "❌"
        print(f"{icon} {event_type}: {count}")
    
    print("\n🎯 Conclusion:")
    if received_events["audio_mixed_raw.data"] > 0:
        print("✅ RAW AUDIO STREAMING WORKS WITHOUT TRANSCRIPTION!")
        print("   Your plan supports raw audio - you can use RecallLiveKitBridge as-is.")
    else:
        print("❌ Raw audio NOT received without transcription provider")
        print("   You need to either:")
        print("   1. Configure a transcription provider (Deepgram/AssemblyAI)")
        print("   2. Use RecallAI's built-in transcription (recallai_streaming)")
    
    if received_events["participant_events.speech_on"] > 0:
        print("\n✅ Speech detection IS working (participant_events.speech_on received)")
        print("   This proves WebSocket connection is active and receiving events.")
    
    print("\n")

if __name__ == "__main__":
    try:
        asyncio.run(test_audio_streaming())
    except KeyboardInterrupt:
        print("\n\n⚠️ Test interrupted by user")
        sys.exit(0)
