"""Tests for the Telegram fetch button endpoint."""

from types import SimpleNamespace
import asyncio

import httpx

import dashboard.app as app_mod


class FakeTelegramSearch:
    jobs = []

    def __init__(self):
        self.called_query = None

    def fetch(self, query, **kwargs):
        self.called_query = query
        return self.jobs


def enable_telegram(monkeypatch, tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  telegram: { enabled: true }\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_mod, "_project_root", str(tmp_path))
    monkeypatch.setattr("hirevia.sources.telegram.TelegramSearch", FakeTelegramSearch)


def make_job(url, message_id, title="Python Developer"):
    return SimpleNamespace(
        title=title,
        company="Example",
        location="Remote",
        url=url,
        description="Hiring for a Python Developer",
        salary="",
        source="Telegram",
        source_type="Telegram",
        original_url=url,
        source_metadata={
            "telegram_message_url": f"https://t.me/channel/{message_id}",
            "channel_username": "@channel",
            "telegram_message_id": str(message_id),
        },
        remote=True,
        country="",
        location_restrictions=[],
        timezone="",
        india_eligibility="Unknown",
        tags=["Python"],
        posted="",
        score=0,
        rating="",
        reasoning="",
        skills_match=0,
        experience_fit=0,
        salary_fit=0,
        remote_fit=0,
    )


def test_fetch_endpoint_passes_keyword_and_saves_new_job(monkeypatch, tmp_path):
    enable_telegram(monkeypatch, tmp_path)
    job = make_job("https://apply.example/jobs/1", 1)
    FakeTelegramSearch.jobs = [job]
    stored = []
    monkeypatch.setattr(app_mod.db, "get_jobs", lambda **kwargs: [])
    monkeypatch.setattr(app_mod.db, "upsert_job", lambda data: stored.append(data))
    monkeypatch.setattr(app_mod.db, "log_activity", lambda *args: None)

    result = app_mod.fetch_telegram_jobs(app_mod.TelegramFetchRequest(query="Python Developer"))

    assert result["success"] is True
    assert result["new_jobs"] == 1
    assert result["duplicates"] == 0
    assert stored[0]["source"] == "Telegram"


def test_fetch_endpoint_empty_query_and_duplicates(monkeypatch, tmp_path):
    enable_telegram(monkeypatch, tmp_path)
    job = make_job("https://apply.example/jobs/1", 1)
    FakeTelegramSearch.jobs = [job, job]
    stored = []
    monkeypatch.setattr(app_mod.db, "get_jobs", lambda **kwargs: [])
    monkeypatch.setattr(app_mod.db, "upsert_job", lambda data: stored.append(data))
    monkeypatch.setattr(app_mod.db, "log_activity", lambda *args: None)

    result = app_mod.fetch_telegram_jobs(app_mod.TelegramFetchRequest())

    assert result["new_jobs"] == 1
    assert result["duplicates"] == 1
    assert len(stored) == 1


def test_fetch_endpoint_disabled_does_not_initialize(monkeypatch, tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  telegram: { enabled: false }\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_mod, "_project_root", str(tmp_path))
    called = []
    monkeypatch.setattr("hirevia.sources.telegram.TelegramSearch", lambda: called.append(True))

    result = app_mod.fetch_telegram_jobs(app_mod.TelegramFetchRequest())

    assert result["status"] == "disabled"
    assert called == []


def test_fetch_endpoint_is_registered_as_http_post():
    async def request():
        from dashboard.app import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/telegram/fetch", json={"query": ""})

    response = asyncio.run(request())
    assert response.status_code == 200
    assert "success" in response.json()
