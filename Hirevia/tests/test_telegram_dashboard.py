"""Tests for the Telegram-only dashboard view."""

import asyncio

import httpx

import dashboard.app as app_mod


class _ASGITestClient:
    def __init__(self, application):
        self.application = application

    def get(self, url):
        async def request():
            transport = httpx.ASGITransport(app=self.application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.get(url)

        return asyncio.run(request())


def test_telegram_endpoint_filters_existing_database_records(monkeypatch):
    calls = {}

    def fake_get_jobs(**kwargs):
        calls.update(kwargs)
        return [
            {
                "id": 1,
                "source": "Telegram",
                "title": "Python Developer",
                "company": "Example",
                "created_at": 1,
                "remote": 1,
            }
        ]

    monkeypatch.setattr(app_mod.db, "get_jobs", fake_get_jobs)
    response = _ASGITestClient(app_mod.app).get("/api/jobs/telegram")

    assert response.status_code == 200
    assert calls["source"] == "Telegram"
    assert response.json()["count"] == 1
    assert response.json()["jobs"][0]["source"] == "Telegram"
    assert response.json()["stats"] == {"total": 1, "recent": 0, "remote": 1}


def test_telegram_endpoint_empty_state_payload(monkeypatch):
    monkeypatch.setattr(app_mod.db, "get_jobs", lambda **kwargs: [])
    response = _ASGITestClient(app_mod.app).get("/api/jobs/telegram")

    assert response.status_code == 200
    assert response.json() == {
        "jobs": [],
        "count": 0,
        "stats": {"total": 0, "recent": 0, "remote": 0},
    }
