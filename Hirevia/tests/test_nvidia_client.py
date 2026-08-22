"""Tests for the three-model NVIDIA client."""

from types import SimpleNamespace

from hirevia.nvidia_client import NVIDIAClient, NVIDIAJobAnalysis


def configured_client(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://nvidia.test/v1")
    monkeypatch.setenv("QUERY_MODEL", "query-model")
    monkeypatch.setenv("JOB_ANALYSIS_MODEL", "job-model")
    monkeypatch.setenv("RANKING_MODEL", "ranking-model")
    monkeypatch.setenv("LLM_ENABLED", "true")
    return NVIDIAClient(timeout=1)


def test_models_are_configured_without_exposing_key(monkeypatch):
    client = configured_client(monkeypatch)
    assert client.available is True
    assert (client.query_model, client.job_model, client.ranking_model) == (
        "query-model", "job-model", "ranking-model"
    )
    assert client.api_key == "test-key"


def test_query_analysis_and_job_analysis_use_separate_models(monkeypatch):
    client = configured_client(monkeypatch)
    calls = []

    def fake_complete(model, system, user):
        calls.append((model, system, user))
        if model == "query-model":
            return {"primary_role": "Python Developer", "technologies": ["Python"], "confidence": 0.9}
        return {
            "relevant": True, "relevance_score": 92, "role_match": 95,
            "skill_match": 90, "technology_match": 95,
            "matched_requirements": ["Python"], "reasons": ["Python is required"],
        }

    monkeypatch.setattr(client, "_complete", fake_complete)
    intent = client.analyze_query("Python Developer")
    job = SimpleNamespace(title="Python Developer", company="Acme", location="Remote", url="https://acme.test/jobs/1", description="Python backend", tags=["Python"], source="Test", posted="", remote=True)
    results = client.analyze_jobs("Python Developer", intent, [job])

    assert calls[0][0] == "query-model"
    assert calls[1][0] == "job-model"
    assert results[client.job_identity(job)].relevance_score == 92


def test_invalid_json_and_api_failures_fall_back(monkeypatch):
    client = configured_client(monkeypatch)
    monkeypatch.setattr(client, "_complete", lambda *args: None)
    intent = client.analyze_query("Data Scientist")
    assert intent.original_query == "Data Scientist"
    assert intent.primary_role == "Data Scientist"
    job = SimpleNamespace(title="Data Scientist", company="Acme", location="Remote", url="u", description="", tags=[], source="Test", posted="", remote=True)
    assert client.analyze_jobs("Data Scientist", intent, [job]) == {}


def test_ranking_returns_only_valid_analyzed_jobs(monkeypatch):
    client = configured_client(monkeypatch)
    client._complete = lambda model, system, user: {"ranked_jobs": [{"job_id": "https://a.test/job", "rank": 1, "final_score": 94, "reason": "Strong match"}]}
    intent = client.analyze_query("Python Developer")
    job = SimpleNamespace(title="Python Developer", company="Acme", location="Remote", url="https://a.test/job", description="Python backend", tags=["Python"], source="Test", posted="", remote=True, source_metadata={})
    analyses = {client.job_identity(job): NVIDIAJobAnalysis(True, 90)}
    ranked = client.rank_jobs("Python Developer", intent, [job], analyses)
    assert ranked[client.job_identity(job)]["final_score"] == 94


def test_no_ai_disables_client(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LLM_ENABLED", "false")
    assert NVIDIAClient().available is False
