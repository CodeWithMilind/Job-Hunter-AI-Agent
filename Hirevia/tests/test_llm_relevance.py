"""Tests for optional LLM query understanding and semantic relevance."""

from types import SimpleNamespace

from hirevia.llm_relevance import LLMJobResult, LLMRelevance
from hirevia.models import Job
from hirevia.quality import is_relevant


def make_job(title="Python Developer", description="Python and FastAPI backend role"):
    return Job(title=title, company="Acme", location="Remote", url="https://acme.test/jobs/1", description=description, tags=["Python"])


def test_query_understanding_strict_json_and_cache(monkeypatch):
    client = LLMRelevance(base_url="http://local")
    calls = []
    monkeypatch.setattr(client, "_call", lambda prompt: calls.append(prompt) or {
        "target_roles": ["Python Engineer"],
        "required_skills": ["Python"],
        "confidence": 0.95,
    })
    first = client.understand_query("Python Developer")
    second = client.understand_query(" python   developer ")
    assert first.required_skills == ["Python"]
    assert second.target_roles == ["Python Engineer"]
    assert len(calls) == 1


def test_invalid_llm_json_falls_back_without_crash(monkeypatch):
    client = LLMRelevance(base_url="http://local")
    monkeypatch.setattr(client, "_call", lambda prompt: None)
    intent = client.understand_query("Data Scientist")
    assert intent.original_query == "Data Scientist"
    assert intent.target_roles == ["Data Scientist"]
    assert client.evaluate_jobs("Data Scientist", intent, [make_job()]) == {}


def test_incomplete_llm_result_is_ignored(monkeypatch):
    client = LLMRelevance(base_url="http://local")
    monkeypatch.setattr(client, "_call", lambda prompt: {"relevant": True})
    intent = client.understand_query("Python Developer Incomplete")
    assert client.evaluate_jobs("Python Developer Incomplete", intent, [make_job()]) == {}


def test_llm_job_result_contains_user_facing_fields(monkeypatch):
    client = LLMRelevance(base_url="http://local")
    monkeypatch.setattr(client, "_call", lambda prompt: {
        "relevant": False,
        "score": 8,
        "reason": "Store Manager has no Python development requirements.",
        "matched_skills": [],
        "missing_skills": ["Python"],
        "confidence": 0.98,
    })
    intent = client.understand_query("Store Manager Relevance")
    result = list(client.evaluate_jobs("Store Manager Relevance", intent, [make_job("Store Manager", "Retail operations")]).values())[0]
    assert result.relevant is False
    assert result.score == 8
    assert result.missing_skills == ["Python"]


def test_semantic_result_is_structured_and_cached(monkeypatch):
    client = LLMRelevance(base_url="http://local")
    calls = []
    monkeypatch.setattr(client, "_call", lambda prompt: calls.append(prompt) or {
        "relevant": True,
        "score": 92,
        "reason": "Python is required for backend work.",
        "matched_skills": ["Python", "FastAPI"],
        "missing_skills": [],
        "confidence": 0.94,
    })
    intent = client.understand_query("Python Developer Cached")
    result = client.evaluate_jobs("Python Developer Cached", intent, [make_job()])
    again = client.evaluate_jobs("Python Developer Cached", intent, [make_job()])
    assert list(result.values())[0].score == 92
    assert list(again.values())[0].relevant is True
    assert len(calls) == 2


def test_invalid_job_intent_is_rejected_deterministically():
    assert not is_relevant("sock", make_job())
    assert not is_relevant("Python Developer", make_job("Store Manager", "Retail operations"))


def test_data_scientist_does_not_accept_adjacent_titles_without_science_content():
    assert is_relevant("Data Scientist", make_job("Data Scientist", "Machine learning, statistics, and experiments"))
    assert not is_relevant("Data Scientist", make_job("Data Engineer", "ETL pipelines, Airflow, and Spark"))
    assert not is_relevant("Data Scientist", make_job("Data Center Engineer", "Data center operations and infrastructure"))


def test_no_ai_does_not_require_llm(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    client = LLMRelevance(base_url="http://local")
    assert client.enabled is False
    assert client.understand_query("Python Developer").target_roles == ["Python Developer"]
