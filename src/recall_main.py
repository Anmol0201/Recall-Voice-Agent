"""
Recall.ai Google Meet Voice Agent - Main Entry Point

This module provides the complete integration for running your voice AI bot
in Google Meet meetings via Recall.ai.

Usage:
    # Start the full service (WebSocket + API + Session Coordinator)
    python -m src.recall_main
    
    # Or programmatically:
    from src.recall_main import start_recall_agent
    await start_recall_agent(meeting_url="https://meet.google.com/xxx-xxxx-xxx")
"""
import asyncio
import logging
import os
import sys
from typing import Optional

from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recall_manager import RecallBotManager
from recall_websocket import RecallWebSocketServer
from recall_api import app, set_recall_manager, set_session_coordinator
from session_coordinator import SessionCoordinator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)

logger = logging.getLogger("recall-main")


class RecallVoiceAgent:
    """
    Main class for running the Recall.ai voice agent.
    
    Combines:
    - RecallBotManager: Creates and manages bots in meetings
    - RecallWebSocketServer: Receives real-time transcripts/audio
    - SessionCoordinator: Orchestrates LLM pipeline and TTS
    - FastAPI: HTTP API for meeting management
    """
    
    def __init__(
        self,
        recall_api_key: str,
        websocket_host: str = "0.0.0.0",
        websocket_port: int = 8765,
        api_host: str = "0.0.0.0",
        api_port: int = 8000,
        public_websocket_url: Optional[str] = None,
        public_webhook_url: Optional[str] = None,
        google_credentials_file: Optional[str] = None,
        llm_api_url: Optional[str] = None,
        recall_region: str = "us-east-1",
    ):
        """
        Initialize the Recall Voice Agent.
        
        Args:
            recall_api_key: Your Recall.ai API key
            websocket_host: Host for WebSocket server
            websocket_port: Port for WebSocket server
            api_host: Host for HTTP API server
            api_port: Port for HTTP API server
            public_websocket_url: Public URL for Recall.ai to connect to WebSocket
            public_webhook_url: Public URL for Recall.ai webhooks
            google_credentials_file: Path to Google Cloud credentials for TTS
            llm_api_url: Your LLM API URL (optional)
            recall_region: Recall.ai API region
        """
        self.websocket_host = websocket_host
        self.websocket_port = websocket_port
        self.api_host = api_host
        self.api_port = api_port
        
        # Initialize session coordinator (handles everything)
        self.coordinator = SessionCoordinator(
            recall_api_key=recall_api_key,
            websocket_host=websocket_host,
            websocket_port=websocket_port,
            webhook_url=public_webhook_url,
            llm_api_url=llm_api_url,
            google_credentials_file=google_credentials_file,
            recall_region=recall_region,
        )
        
        # Set up API with coordinator's managers
        set_recall_manager(self.coordinator.recall_manager)
        set_session_coordinator(self.coordinator)
        
        self._api_server = None
        self._running = False
        
        logger.info(f"RecallVoiceAgent initialized")
        logger.info(f"  - WebSocket: {websocket_host}:{websocket_port}")
        logger.info(f"  - HTTP API: {api_host}:{api_port}")
    
    async def start(self):
        """Start all services"""
        logger.info("=" * 60)
        logger.info("Starting Recall.ai Voice Agent...")
        logger.info("=" * 60)
        
        self._running = True
        
        # Start session coordinator (includes WebSocket server)
        await self.coordinator.start()
        
        # Start API server in background
        import uvicorn
        config = uvicorn.Config(
            app,
            host=self.api_host,
            port=self.api_port,
            log_level="info",
        )
        self._api_server = uvicorn.Server(config)
        
        # Run API server as task
        asyncio.create_task(self._api_server.serve())
        
        logger.info("=" * 60)
        logger.info("Recall.ai Voice Agent is running!")
        logger.info(f"  - API Server: http://{self.api_host}:{self.api_port}")
        logger.info(f"  - WebSocket: ws://{self.websocket_host}:{self.websocket_port}")
        logger.info("")
        logger.info("To join a meeting, POST to /meet/join with:")
        logger.info('  {"meeting_url": "https://meet.google.com/xxx-xxxx-xxx"}')
        logger.info("=" * 60)
    
    async def stop(self):
        """Stop all services"""
        logger.info("Stopping Recall.ai Voice Agent...")
        self._running = False
        
        # Stop coordinator
        await self.coordinator.stop()
        
        # Stop API server
        if self._api_server:
            self._api_server.should_exit = True
        
        logger.info("Recall.ai Voice Agent stopped")
    
    async def join_meeting(
        self,
        meeting_url: str,
        bot_name: str = "Jarvis",
    ) -> str:
        """
        Join a Google Meet meeting.
        
        Args:
            meeting_url: Google Meet URL
            bot_name: Display name for the bot
            
        Returns:
            str: Bot ID for the session
        """
        session = await self.coordinator.join_meeting(meeting_url, bot_name)
        return session.bot_id
    
    async def leave_meeting(self, bot_id: str):
        """Leave a meeting"""
        await self.coordinator.leave_meeting(bot_id)


async def start_recall_agent(
    meeting_url: Optional[str] = None,
    bot_name: str = "Jarvis",
    auto_join: bool = True,
):
    """
    Start the Recall.ai voice agent.
    
    Args:
        meeting_url: Optional meeting URL to join immediately
        bot_name: Bot display name
        auto_join: Whether to auto-join the meeting_url
    """
    load_dotenv(".env.local")
    
    # Get configuration
    recall_api_key = os.getenv("RECALL_API_KEY")
    websocket_port = int(os.getenv("WEBSOCKET_PORT", "8765"))
    api_port = int(os.getenv("API_PORT", "8000"))
    
    if not recall_api_key:
        logger.error("❌ RECALL_API_KEY is not set in .env.local")
        raise ValueError("RECALL_API_KEY is required")
    
    # Get public URLs (optional for local testing)
    public_websocket_url = os.getenv("PUBLIC_WEBSOCKET_URL")
    public_webhook_url = os.getenv("PUBLIC_WEBHOOK_URL")
    
    if not public_websocket_url or not public_webhook_url:
        logger.warning("=" * 60)
        logger.warning("⚠️  LOCAL TESTING MODE")
        logger.warning("PUBLIC_WEBSOCKET_URL and/or PUBLIC_WEBHOOK_URL not set")
        logger.warning("Bot will be created but Recall.ai cannot connect back")
        logger.warning("")
        logger.warning("To enable full functionality, use Cloudflare Tunnel:")
        logger.warning("  Terminal 1: cloudflared tunnel --url http://localhost:8765")
        logger.warning("  Terminal 2: cloudflared tunnel --url http://localhost:8000")
        logger.warning("Then add URLs to .env.local")
        logger.warning("=" * 60)
    
    # Create agent
    agent = RecallVoiceAgent(
        recall_api_key=recall_api_key,
        websocket_port=websocket_port,
        api_port=api_port,
        public_websocket_url=public_websocket_url,
        public_webhook_url=public_webhook_url,
        google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE"),
        llm_api_url=os.getenv("AGENT_API_URL"),
        recall_region=os.getenv("RECALL_REGION", "us-east-1"),
    )
    
    # Start services
    await agent.start()
    
    # Auto-join meeting if provided
    if auto_join and meeting_url:
        try:
            bot_id = await agent.join_meeting(meeting_url, bot_name)
            logger.info(f"✅ Bot joining meeting: {bot_id}")
        except Exception as e:
            logger.error(f"❌ Failed to join meeting: {e}")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal...")
    finally:
        await agent.stop()


def main():
    """Command line entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Recall.ai Voice Agent")
    parser.add_argument(
        "--meeting-url",
        type=str,
        default=os.getenv("TEST_MEETING_URL"),
        help="Google Meet URL to join",
    )
    parser.add_argument(
        "--bot-name",
        type=str,
        default=os.getenv("BOT_NAME", "Jarvis"),
        help="Bot display name",
    )
    parser.add_argument(
        "--no-auto-join",
        action="store_true",
        help="Don't auto-join meeting, just start servers",
    )
    
    args = parser.parse_args()
    
    asyncio.run(start_recall_agent(
        meeting_url=args.meeting_url,
        bot_name=args.bot_name,
        auto_join=not args.no_auto_join,
    ))


if __name__ == "__main__":
    main()
