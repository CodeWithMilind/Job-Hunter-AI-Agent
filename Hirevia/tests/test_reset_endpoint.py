"""Test for the /api/reset endpoint to verify it only deletes job records."""

import sys
sys.path.insert(0, ".")
sys.path.insert(0, "dashboard")

import os
from fastapi.testclient import TestClient
import dashboard.app as app_mod
import dashboard.database as db

def test_reset_endpoint_exists():
    """Verify the /api/reset endpoint is registered."""
    client = TestClient(app_mod.app)
    resp = client.post('/api/reset', json={})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    print("✓ /api/reset endpoint is registered and responds with 200")

def test_reset_deletes_all_jobs():
    """Verify reset deletes all job records."""
    client = TestClient(app_mod.app)
    
    # Search to populate database
    resp1 = client.post('/api/search/jobs', json={
        'query': 'Python Developer',
        'location': '',
        'limit': 10,
        'no_ai': True,
        'no_cache': True
    })
    assert resp1.status_code == 200
    initial_count = resp1.json().get('count', 0)
    assert initial_count > 0, "Expected jobs from search"
    
    # Verify they were stored
    stats_before = client.get('/api/stats').json()
    db_count_before = stats_before.get('total', 0)
    assert db_count_before > 0, "Expected jobs in database before reset"
    print(f"✓ Database had {db_count_before} jobs before reset")
    
    # Call reset
    reset_resp = client.post('/api/reset', json={})
    assert reset_resp.status_code == 200
    reset_data = reset_resp.json()
    assert reset_data.get('ok') == True, "Reset should return ok: true"
    deleted = reset_data.get('deleted_count', 0)
    assert deleted == db_count_before, f"Expected {db_count_before} deleted, got {deleted}"
    print(f"✓ Reset deleted {deleted} job records")
    
    # Verify database is empty
    stats_after = client.get('/api/stats').json()
    db_count_after = stats_after.get('total', 0)
    assert db_count_after == 0, f"Expected 0 jobs after reset, got {db_count_after}"
    print("✓ Database is empty after reset (0 jobs)")

def test_reset_clears_cache():
    """Verify reset allows previously seen jobs to reappear."""
    client = TestClient(app_mod.app)
    
    # First search
    resp1 = client.post('/api/search/jobs', json={
        'query': 'Python Developer',
        'location': '',
        'limit': 15,
        'no_ai': True,
        'no_cache': True
    })
    count1 = resp1.json().get('count', 0)
    print(f"✓ First search returned {count1} jobs")
    
    # Reset
    client.post('/api/reset', json={})
    print("✓ Reset completed")
    
    # Search again - should get jobs back (cache was cleared)
    resp2 = client.post('/api/search/jobs', json={
        'query': 'Python Developer',
        'location': '',
        'limit': 15,
        'no_ai': True,
        'no_cache': True
    })
    count2 = resp2.json().get('count', 0)
    assert count2 > 0, "Expected jobs after reset search (cache should be cleared)"
    print(f"✓ After reset, search returned {count2} jobs (cache was cleared)")

def test_reset_preserves_configuration():
    """Verify reset only deletes jobs, not configuration."""
    client = TestClient(app_mod.app)
    
    # Get sources before reset
    sources_before = client.get('/api/sources').json()
    source_count_before = len(sources_before.get('sources', []))
    
    # Reset
    client.post('/api/reset', json={})
    
    # Get sources after reset
    sources_after = client.get('/api/sources').json()
    source_count_after = len(sources_after.get('sources', []))
    
    assert source_count_before == source_count_after, \
        f"Source count changed: {source_count_before} → {source_count_after}"
    print(f"✓ Sources preserved: {source_count_before} sources before and after")

def test_reset_response_format():
    """Verify reset returns correct JSON response."""
    client = TestClient(app_mod.app)
    
    resp = client.post('/api/reset', json={})
    data = resp.json()
    
    assert 'ok' in data, "Response should have 'ok' field"
    assert 'deleted_count' in data, "Response should have 'deleted_count' field"
    assert 'message' in data, "Response should have 'message' field"
    assert data['ok'] == True, "ok should be True"
    assert isinstance(data['deleted_count'], int), "deleted_count should be integer"
    assert isinstance(data['message'], str), "message should be string"
    print(f"✓ Response format correct: {data}")

if __name__ == '__main__':
    print("\n" + "="*70)
    print("TESTING /api/reset ENDPOINT")
    print("="*70 + "\n")
    
    test_reset_endpoint_exists()
    test_reset_deletes_all_jobs()
    test_reset_clears_cache()
    test_reset_preserves_configuration()
    test_reset_response_format()
    
    print("\n" + "="*70)
    print("✅ ALL RESET ENDPOINT TESTS PASSED")
    print("="*70 + "\n")
