import os
import re
import json
import asyncio
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

SESSION = "job_hunter_session"
STATE_FILE = Path("telegram_state.json")


def load_channels():
    with open("telegram.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f).get("channels", [])


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8"
    )


def clean_text(text):
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"[🚨🚀🔥😍⚡🎓💼📍💰🏢📝🤖📌🎯🛠️]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_job_message(text):
    lower = text.lower()

    # Ignore obvious non-job/promotional posts
    blocked = [
        "masterclass",
        "webinar",
        "course",
        "certificate",
        "follow us",
        "join our",
        "missing job alerts",
        "₹499 for lifetime",
        "workshop",
    ]

    if any(x in lower for x in blocked):
        return False

    job_keywords = [
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

    return any(x in lower for x in job_keywords)


def extract_url(text):
    urls = re.findall(r"https?://[^\s<>]+", text)
    return urls[0].rstrip(").,") if urls else ""


def extract_company(text):
    patterns = [
        r"company\s*(?:name)?\s*[:\-]\s*(.+)",
        r"🏢\s*company\s*[:\-]\s*(.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

    # Detect common company heading patterns
    lines = [clean_text(x) for x in text.splitlines() if x.strip()]

    for line in lines[:8]:
        upper = line.upper()

        if any(x in upper for x in [
            "SONY RESEARCH INDIA",
            "CISCO",
            "ACCENTURE",
            "DELOITTE",
            "TCS",
            "HPE",
            "PITNEY BOWES",
            "COGNIZANT",
            "TELUS DIGITAL",
            "TOWER RESEARCH CAPITAL",
        ]):
            return line.strip(":- ")

    return "Unknown"


def extract_role(text):
    patterns = [
        r"role\s*[:\-]\s*(.+)",
        r"position\s*[:\-]\s*(.+)",
        r"job\s*title\s*[:\-]\s*(.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

    # Look for common role lines
    for line in text.splitlines():
        cleaned = clean_text(line)

        if re.search(
            r"\b(developer|engineer|intern|analyst|scientist|trainee|manager)\b",
            cleaned,
            re.IGNORECASE,
        ):
            return cleaned[:150]

    return "Unknown"


def extract_location(text):
    patterns = [
        r"location\s*[:\-]\s*(.+)",
        r"work\s*mode\s*[:\-]\s*(.+)",
        r"📍\s*(.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

    if re.search(r"\b(remote|work from home|wfh)\b", text, re.IGNORECASE):
        return "Remote"

    return "Unknown"


def parse_job(message, channel_name):
    text = message.text or ""

    if not is_job_message(text):
        return None

    return {
        "title": extract_role(text),
        "company": extract_company(text),
        "location": extract_location(text),
        "url": extract_url(text),
        "description": text,
        "source": "Telegram",
        "source_type": "Telegram",
        "channel": channel_name,
        "telegram_message_id": message.id,
        "telegram_date": message.date.isoformat() if message.date else None,
    }


async def fetch_channel(client, channel, state):
    username = channel["username"]
    channel_name = channel.get("name", username)
    limit = channel.get("limit", 20)

    last_id = state.get(username, 0)

    print(f"\nChecking {username}")
    print(f"Last processed message ID: {last_id}")

    jobs = []
    newest_id = last_id

    async for message in client.iter_messages(username, limit=limit):

        # Already processed
        if message.id <= last_id:
            continue

        if message.id > newest_id:
            newest_id = message.id

        job = parse_job(message, channel_name)

        if job:
            jobs.append(job)

    # Save progress only after successful channel processing
    state[username] = newest_id

    print(f"New job posts: {len(jobs)}")

    return jobs


async def main():
    channels = load_channels()
    state = load_state()

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()

    all_jobs = []

    for channel in channels:
        if not channel.get("enabled", True):
            continue

        try:
            jobs = await fetch_channel(client, channel, state)
            all_jobs.extend(jobs)

        except Exception as e:
            print(f"ERROR: {channel['username']} -> {e}")

    await client.disconnect()

    save_state(state)

    print(f"\nTotal NEW Telegram jobs: {len(all_jobs)}")

    for job in all_jobs:
        print("\n" + "-" * 60)
        print("TITLE:", job["title"])
        print("COMPANY:", job["company"])
        print("LOCATION:", job["location"])
        print("URL:", job["url"])
        print("MESSAGE ID:", job["telegram_message_id"])


if __name__ == "__main__":
    asyncio.run(main())