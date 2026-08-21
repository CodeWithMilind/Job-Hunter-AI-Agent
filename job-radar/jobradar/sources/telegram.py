from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient

from jobradar.models import Job
from jobradar.sources.base import JobSource

load_dotenv()

STATE_FILE = Path("telegram_state.json")


class TelegramSearch(JobSource):
    source_id = "telegram"
    name = "Telegram"
    source_type = "Telegram"
    enabled = True
    metadata = {"description": "Configured Telegram job channels"}

    def __init__(self):
        self._channels = self._load_channels()

    @staticmethod
    def _load_channels():
        path = Path("telegram.yaml")
        if not path.exists():
            return []

        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f).get("channels", [])

    @staticmethod
    def _load_state():
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {}

    @staticmethod
    def _save_state(state):
        STATE_FILE.write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _is_job(text: str) -> bool:
        text = text.lower()

        blocked = [
            "masterclass",
            "webinar",
            "course",
            "certificate",
            "follow us",
            "join our",
            "workshop",
        ]

        if any(word in text for word in blocked):
            return False

        keywords = [
            "hiring",
            "job opportunity",
            "job opening",
            "role:",
            "position:",
            "developer",
            "engineer",
            "intern",
            "internship",
            "fresher",
            "vacancy",
            "apply now",
            "apply:",
            "graduate trainee",
            "software engineer",
            "data scientist",
            "data analyst",
        ]

        return any(word in text for word in keywords)

    @staticmethod
    def _url(text: str) -> str:
        import re

        urls = re.findall(r"https?://[^\s<>]+", text)
        return urls[0].rstrip(").,") if urls else ""

    @staticmethod
    def _field(text: str, patterns: list[str]) -> str:
        import re

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return re.sub(r"[*_`~]", "", match.group(1)).strip()
        return ""

    def _parse(self, message, channel_name: str) -> Job | None:
        text = message.text or ""

        if not self._is_job(text):
            return None

        title = self._field(
            text,
            [
                r"role\s*[:\-]\s*(.+)",
                r"position\s*[:\-]\s*(.+)",
                r"job\s*title\s*[:\-]\s*(.+)",
            ],
        )

        company = self._field(
            text,
            [
                r"company\s*(?:name)?\s*[:\-]\s*(.+)",
            ],
        )

        location = self._field(
            text,
            [
                r"location\s*[:\-]\s*(.+)",
                r"work\s*mode\s*[:\-]\s*(.+)",
            ],
        )

        if not location:
            location = "Remote" if "remote" in text.lower() else "Unknown"

        if not title:
            title = "Unknown"

        if not company:
            company = "Unknown"

        return Job(
            title=title,
            company=company,
            location=location,
            url=self._url(text),
            description=text,
            source="Telegram",
            source_type="Telegram",
            original_url=self._url(text),
            source_metadata={
                "channel": channel_name,
                "telegram_message_id": str(message.id),
            },
            remote="remote" in text.lower() or "work from home" in text.lower(),
            posted=message.date.isoformat() if message.date else "",
        )

    async def _fetch(self, limit: int) -> List[Job]:
        api_id = int(os.environ["TELEGRAM_API_ID"])
        api_hash = os.environ["TELEGRAM_API_HASH"]

        state = self._load_state()
        client = TelegramClient("job_hunter_session", api_id, api_hash)

        jobs = []

        await client.start()

        try:
            for channel in self._channels:
                if not channel.get("enabled", True):
                    continue

                username = channel["username"]
                channel_name = channel.get("name", username)
                last_id = state.get(username, 0)
                newest_id = last_id

                async for message in client.iter_messages(
                    username,
                    limit=channel.get("limit", limit),
                ):
                    if message.id <= last_id:
                        continue

                    newest_id = max(newest_id, message.id)

                    job = self._parse(message, channel_name)

                    if job:
                        jobs.append(job)

                state[username] = newest_id

        finally:
            await client.disconnect()

        self._save_state(state)

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
        return asyncio.run(self._fetch(limit))