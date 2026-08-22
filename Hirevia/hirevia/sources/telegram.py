from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

try:
    from telethon import TelegramClient
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    TelegramClient = None

from hirevia.models import Job
from hirevia.sources.base import JobSource

load_dotenv()

logger = logging.getLogger(__name__)


class TelegramSearch(JobSource):
    source_id = "telegram"
    name = "Telegram"
    source_type = "Telegram"
    enabled = True
    metadata = {"description": "Configured Telegram job channels"}

    def __init__(self):
        self._channels = self._load_channels()
        self.last_error = ""
        self.last_scan_stats = {"channels_checked": 0, "messages_fetched": 0, "jobs_extracted": 0}

    @staticmethod
    def _get_config_dir() -> Path:
        """Find project root by locating sources.yaml."""
        current = Path.cwd()
        # Search up to 3 levels for project root
        for _ in range(3):
            if (current / "sources.yaml").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        # Fall back to current directory
        return Path.cwd()

    @staticmethod
    def _load_channels():
        config_dir = TelegramSearch._get_config_dir()
        path = config_dir / "telegram.yaml"
        if not path.exists():
            return []

        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                return []
            return data.get("channels", [])

    @staticmethod
    def _get_state_file() -> Path:
        """Get session state file in project root."""
        config_dir = TelegramSearch._get_config_dir()
        return config_dir / "telegram_state.json"

    @staticmethod
    def _get_session_file() -> str:
        """Use one persistent Telethon session regardless of launch directory."""
        return str(TelegramSearch._get_config_dir() / "job_hunter_session")

    @staticmethod
    def _load_state():
        state_file = TelegramSearch._get_state_file()
        if state_file.exists():
            return json.loads(state_file.read_text(encoding="utf-8"))
        return {}

    @staticmethod
    def _save_state(state):
        state_file = TelegramSearch._get_state_file()
        state_file.write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _is_job(text: str) -> bool:
        """Detect if a Telegram message contains a job posting."""
        if not text:
            return False
            
        text_lower = text.lower()

        # Ignore obvious non-job content
        blocked = [
            "masterclass",
            "webinar",
            "course",
            "certificate",
            "follow us",
            "join our",
            "workshop",
            "seminar",
            "bootcamp",
            "training program",
            "learn now",
            "register here",
            "exclusive offer",
            "limited time",
            "only for",
            "hurry up",
            "discount",
            "promo",
            "sale",
            "free trial",
        ]

        if any(word in text_lower for word in blocked):
            return False

        # Keywords that indicate a job posting
        keywords = [
            "we're hiring",
            "hiring",
            "job opportunity",
            "job opening",
            "role:",
            "position:",
            "vacancy",
            "opening:",
            "apply now",
            "apply:",
            "apply here",
            "recruitment",
            "experienced",
            "experience required",
            "required:",
            "skills:",
            "salary:",
            "ctc:",
            "package:",
            "developer",
            "engineer",
            "intern",
            "internship",
            "fresher",
            "trainee",
            "graduate",
            "software engineer",
            "data scientist",
            "data analyst",
            "business analyst",
            "qa engineer",
            "product manager",
            "manager",
            "analyst",
            "senior",
            "junior",
            "lead",
        ]

        return any(word in text_lower for word in keywords)

    @staticmethod
    def _url(text: str) -> str:
        """Extract the first URL from text, preferring application URLs."""
        import re

        urls = re.findall(r"https?://[^\s<>]+", text)
        if not urls:
            return ""
        
        # Filter out common non-application URLs
        blocked_domains = ["t.me", "telegram.org", "telegram.me"]
        app_urls = [u.rstrip(").,") for u in urls 
                    if not any(b in u for b in blocked_domains)]
        
        if app_urls:
            return app_urls[0]
        
        # Fall back to first URL even if Telegram
        return urls[0].rstrip(").,") if urls else ""

    @staticmethod
    def _telegram_message_url(channel_username: str, message_id: int) -> str:
        """Generate a Telegram message URL if the channel is public."""
        if not channel_username:
            return ""
        # Remove @ if present
        username = channel_username.lstrip("@")
        return f"https://t.me/{username}/{message_id}"

    @staticmethod
    def _field(text: str, patterns: list[str]) -> str:
        import re

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return re.sub(r"[*_`~]", "", match.group(1)).strip()
        return ""

    def _parse(self, message, channel_name: str, channel_username: str) -> Job | None:
        """Parse a Telegram message into a Job object."""
        text = message.text or ""

        if not self._is_job(text):
            return None

        title = self._field(
            text,
            [
                r"role\s*[:\-]\s*(.+?)(?:\n|$)",
                r"position\s*[:\-]\s*(.+?)(?:\n|$)",
                r"job\s*title\s*[:\-]\s*(.+?)(?:\n|$)",
                r"hiring\s+(?:for\s+)?(?:a\s+)?(.+?)(?:\n|$)",
            ],
        )

        company = self._field(
            text,
            [
                r"company\s*(?:name)?\s*[:\-]\s*(.+?)(?:\n|$)",
                r"from\s+(.+?)(?:\n|$)",
            ],
        )

        location = self._field(
            text,
            [
                r"location\s*[:\-]\s*(.+?)(?:\n|$)",
                r"work\s*(?:location|mode)\s*[:\-]\s*(.+?)(?:\n|$)",
            ],
        )

        skills = self._field(
            text,
            [
                r"skills?\s*[:\-]\s*(.+?)(?:\n|$)",
                r"technolog(?:y|ies)\s*[:\-]\s*(.+?)(?:\n|$)",
            ],
        )

        remote = "remote" in text.lower() or "work from home" in text.lower()
        
        if not location:
            location = "Remote" if remote else "Unknown"

        if not title:
            title = "Unknown"

        if not company:
            company = "Unknown"

        # Get URLs - prefer non-Telegram URLs
        app_url = self._url(text)
        
        # Generate Telegram message URL
        tg_url = self._telegram_message_url(channel_username, message.id) if channel_username else ""
        
        # If no external URL found, use Telegram URL
        if not app_url:
            app_url = tg_url

        return Job(
            title=title,
            company=company,
            location=location,
            url=app_url or tg_url,
            description=text,
            source="Telegram",
            source_type="Telegram",
            original_url=app_url or tg_url,
            source_metadata={
                "channel": channel_name,
                "channel_username": channel_username,
                "telegram_message_id": str(message.id),
                "telegram_message_url": tg_url,
                "skills": skills,
            },
            tags=[item.strip() for item in skills.split(",") if item.strip()],
            remote=remote,
            posted=message.date.isoformat() if message.date else "",
        )

    async def _fetch(self, query: str, location: str, limit: int) -> List[Job]:
        if TelegramClient is None:
            raise RuntimeError("Telethon is not installed; Telegram source is unavailable")

        api_id = int(os.environ["TELEGRAM_API_ID"])
        api_hash = os.environ["TELEGRAM_API_HASH"]

        state = self._load_state()
        client = TelegramClient(self._get_session_file(), api_id, api_hash)

        jobs = []
        channels_checked = 0
        messages_fetched = 0

        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("Telegram session is not authenticated; Telegram skipped")
            await client.disconnect()
            return []

        try:
            for channel in self._channels:
                if not channel.get("enabled", True):
                    continue
                channels_checked += 1

                username = channel["username"]
                channel_name = channel.get("name", username)
                last_id = state.get(username, 0)
                newest_id = last_id

                try:
                    async for message in client.iter_messages(
                        username,
                        limit=channel.get("limit", limit),
                        min_id=last_id,
                    ):
                        messages_fetched += 1
                        newest_id = max(newest_id, message.id)

                        job = self._parse(message, channel_name, username)

                        # Telegram is a feed source: resume-derived search terms
                        # and searchable-source locations must not hide new posts.
                        if job:
                            jobs.append(job)
                    state[username] = newest_id
                except Exception as exc:
                    logger.warning("Telegram channel %s failed: %s", username, exc)

        finally:
            await client.disconnect()

        self._save_state(state)
        self.last_scan_stats = {
            "channels_checked": channels_checked,
            "messages_fetched": messages_fetched,
            "jobs_extracted": len(jobs),
        }
        logger.info("[TELEGRAM] Channels checked: %d", channels_checked)
        logger.info("[TELEGRAM] Messages fetched: %d", messages_fetched)
        logger.info("[TELEGRAM] Jobs extracted: %d", len(jobs))

        return jobs

    def fetch(
        self,
        query: str,
        *,
        location: str = "",
        limit: int = 50,
        max_pages: int = 3,
        companies_path: str = "companies.yaml",
    ) -> List[Job]:
        """Fetch jobs from configured Telegram channels.
        
        Returns an empty list if Telegram is not configured or unavailable,
        without raising an exception. This allows other sources to continue.
        """
        self.last_error = ""
        if not self.enabled:
            return []
        
        if TelegramClient is None:
            self.last_error = "Telethon is not installed"
            logger.warning("Telethon not installed; Telegram source skipped")
            return []
        
        if not self._channels:
            self.last_error = "No Telegram channels configured"
            logger.warning("No Telegram channels configured (telegram.yaml)")
            return []
        
        try:
            if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
                self.last_error = "TELEGRAM_API_ID/TELEGRAM_API_HASH not set"
                logger.warning(
                    "TELEGRAM_API_ID/TELEGRAM_API_HASH not set; Telegram skipped"
                )
                return []
            
            return asyncio.run(self._fetch(query, location, limit))
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Telegram fetch failed: {e}")
            return []