"""
Recall.ai Bot Manager - Handles bot lifecycle and meeting connections.
"""
import aiohttp
import logging
import json
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

logger = logging.getLogger("recall-manager")
logger.setLevel(logging.INFO)


class BotStatus(Enum):
    """Recall.ai bot status states"""
    READY = "ready"
    JOINING_CALL = "joining_call"
    IN_WAITING_ROOM = "in_waiting_room"
    IN_CALL_NOT_RECORDING = "in_call_not_recording"
    IN_CALL_RECORDING = "in_call_recording"
    CALL_ENDED = "call_ended"
    DONE = "done"
    FATAL = "fatal"
    ANALYSIS_DONE = "analysis_done"


@dataclass
class BotSession:
    """Tracks individual bot sessions"""
    bot_id: str
    meeting_url: str
    bot_name: str
    status: BotStatus = BotStatus.READY
    created_at: datetime = field(default_factory=datetime.now)
    joined_at: Optional[datetime] = None
    left_at: Optional[datetime] = None
    recording_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class RecallBotManager:
    """
    Manages Recall.ai bot lifecycle for Google Meet integration.
    
    Features:
    - Create and join bots to meetings
    - Real-time transcription via WebSocket
    - Audio output for bot responses
    - Bot status monitoring
    """
    
    # Recall.ai API regions
    REGIONS = {
        "us-east-1": "https://us-east-1.recall.ai",
        "us-west-2": "https://us-west-2.recall.ai",
        "eu-west-1": "https://eu-west-1.recall.ai",
    }
    
    def __init__(
        self,
        api_key: str,
        region: str = "us-east-1",
        websocket_url: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ):
        """
        Initialize the Recall.ai Bot Manager.
        
        Args:
            api_key: Your Recall.ai API key
            region: API region (us-east-1, us-west-2, eu-west-1)
            websocket_url: Your WebSocket server URL for real-time audio/transcripts
            webhook_url: Your webhook URL for bot status events
        """
        self.api_key = api_key
        self.base_url = self.REGIONS.get(region, self.REGIONS["us-east-1"])
        self.websocket_url = websocket_url
        self.webhook_url = webhook_url
        self.sessions: dict[str, BotSession] = {}
        
        logger.info(f"RecallBotManager initialized with region: {region}")
    
    def _get_headers(self) -> dict:
        """Get API request headers"""
        return {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    async def create_bot(
    self,
    meeting_url: str,
    bot_name: str = "Jarvis",
    ) -> BotSession:
        """
        Create a Recall.ai bot with:
        - diarized transcription
        - per-participant raw audio
        - speech_on / speech_off events
        """

        events = [
            # Transcription
            "transcript.data",
            "transcript.partial_data",

            # Per-participant audio
            "audio_separate_raw.data",

            # Speaker activity
            "participant_events.speech_on",
            "participant_events.speech_off",
            "participant_events.join",
            "participant_events.leave",
        ]

        realtime_endpoints = [
            {
                "type": "websocket",
                "url": self.websocket_url,
                "events": events,
            }
        ]

        payload = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,

            # ============================
            # RECORDING CONFIG (CRITICAL)
            # ============================
            "recording_config": {

                # ---- TRANSCRIPTION (MANDATORY) ----
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "language_code": "en",
                            "mode": "prioritize_accuracy",
                            "filter_profanity": False,

                            # 🚨 THIS ENABLES speech_on/off
                            "diarization": True,
                        }
                    }
                },

                # ---- PER PARTICIPANT AUDIO ----
                "audio_separate_raw": {
                    "sample_rate": 16000,
                    "encoding": "pcm_s16le",
                },

                # ---- REALTIME EVENTS ----
                "realtime_endpoints": realtime_endpoints,
            },

            # ============================
            # AUDIO OUTPUT (REQUIRED)
            # ============================
            "automatic_audio_output": {
                "in_call_recording": {
                    "data": {
                        "kind": "mp3",
                        "b64_data": "//uQxAAAAAANIAAAAAExBTUUzLjEwMFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVQ=="
                    }
                }
            },

            # ============================
            # AUTO LEAVE
            # ============================
            "automatic_leave": {
                "waiting_room_timeout": 300,
                "noone_joined_timeout": 300,
                "everyone_left_timeout": 30,
            },

            # ============================
            # GOOGLE MEET
            # ============================
            "google_meet": {
                "login_required": False,
            },
        }

        logger.info(f"Creating bot for meeting: {meeting_url}")
        logger.debug(json.dumps(payload, indent=2))

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/v1/bot/",
                headers=self._get_headers(),
                json=payload,
            ) as response:
                if response.status != 201:
                    text = await response.text()
                    raise RuntimeError(f"Recall bot creation failed: {response.status} {text}")

                data = await response.json()
                bot_id = data["id"]

                bot_session = BotSession(
                    bot_id=bot_id,
                    meeting_url=meeting_url,
                    bot_name=bot_name,
                    status=BotStatus.JOINING_CALL,
                    metadata=data,
                )

                self.sessions[bot_id] = bot_session
                logger.info(f"Bot created successfully: {bot_id}")
                return bot_session

    
    async def get_bot_status(self, bot_id: str) -> dict:
        """
        Get the current status of a bot.
        
        Args:
            bot_id: The bot's unique identifier
            
        Returns:
            dict: Bot status information
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/v1/bot/{bot_id}/",
                headers=self._get_headers(),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Update local session
                    if bot_id in self.sessions:
                        status_code = data.get("status_changes", [{}])[-1].get("code", "")
                        try:
                            self.sessions[bot_id].status = BotStatus(status_code)
                        except ValueError:
                            pass
                        self.sessions[bot_id].metadata = data
                    
                    return data
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get bot status: {response.status}")
                    raise Exception(f"Failed to get bot status: {error_text}")
    
    async def output_audio(self, bot_id: str, mp3_b64_data: str) -> bool:
        """
        Send audio output to the meeting (bot speaks).
        
        Args:
            bot_id: The bot's unique identifier
            mp3_b64_data: Base64 encoded MP3 audio data
            
        Returns:
            bool: True if successful
        """
        payload = {
            "kind": "mp3",
            "b64_data": mp3_b64_data,
        }
        
        logger.debug(f"Sending audio output for bot: {bot_id}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/v1/bot/{bot_id}/output_audio/",
                headers=self._get_headers(),
                json=payload,
            ) as response:
                if response.status in [200, 201, 204]:
                    logger.info(f"Audio output sent successfully for bot: {bot_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to send audio output: {response.status} - {error_text}")
                    return False
    
    async def leave_meeting(self, bot_id: str) -> bool:
        """
        Remove the bot from the meeting.
        
        Args:
            bot_id: The bot's unique identifier
            
        Returns:
            bool: True if successful
        """
        logger.info(f"Removing bot from meeting: {bot_id}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/v1/bot/{bot_id}/leave_call/",
                headers=self._get_headers(),
            ) as response:
                if response.status in [200, 201, 204]:
                    if bot_id in self.sessions:
                        self.sessions[bot_id].status = BotStatus.CALL_ENDED
                        self.sessions[bot_id].left_at = datetime.now()
                    logger.info(f"Bot left meeting: {bot_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to remove bot: {response.status} - {error_text}")
                    return False
    
    async def send_chat_message(self, bot_id: str, message: str) -> bool:
        """
        Send a chat message in the meeting.
        
        Args:
            bot_id: The bot's unique identifier
            message: The message to send
            
        Returns:
            bool: True if successful
        """
        payload = {
            "message": message,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/v1/bot/{bot_id}/send_chat_message/",
                headers=self._get_headers(),
                json=payload,
            ) as response:
                if response.status in [200, 201, 204]:
                    logger.info(f"Chat message sent for bot: {bot_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to send chat message: {response.status}")
                    return False
    
    def get_session(self, bot_id: str) -> Optional[BotSession]:
        """Get a bot session by ID"""
        return self.sessions.get(bot_id)
    
    def update_session_status(self, bot_id: str, status: str):
        """Update session status from webhook"""
        if bot_id in self.sessions:
            try:
                self.sessions[bot_id].status = BotStatus(status)
                if status == "in_call_recording":
                    self.sessions[bot_id].joined_at = datetime.now()
            except ValueError:
                logger.warning(f"Unknown status: {status}")