#!/usr/bin/env python
"""Test script to verify the reset endpoint works end-to-end."""

import sys
sys.path.insert(0, ".")
sys.path.insert(0, "dashboard")

from fastapi.testclient import TestClient
import dashboard.app as app_mod

def test_reset_flow():
    """Test: search → check stats → reset → verify empty → search again."""
    client = TestClient(app_mod.app)
    
    print("\n" + "="*60)
    print("RESET FLOW VERIFICATION")
    print("="*60)
    
    # 1. Search to populate database
    print("\n1. Searching for jobs...")
    resp1 = client.post('/api/search/jobs', json={
        'query': 'Python Developer',
        'location': '',
        'limit': 20,
        'no_ai': True,
        'no_cache': True
    })
    count1 = resp1.json().get('count', 0)
    print(f"   ✓ Status: {resp1.status_code}, Jobs returned: {count1}")
    
    # 2. Check stats before reset
    print("\n2. Checking database stats before reset...")
    stats_before = client.get('/api/stats').json()
    total_before = stats_before.get('total', 0)
    print(f"   ✓ Total jobs in database: {total_before}")
    assert total_before > 0, "Expected jobs in database before reset"
    
    # 3. Call reset endpoint
    print("\n3. Calling /api/reset endpoint...")
    reset_resp = client.post('/api/reset', json={})
    assert reset_resp.status_code == 200, f"Reset failed with {reset_resp.status_code}"
    reset_data = reset_resp.json()
    deleted = reset_data.get('deleted_count', 0)
    print(f"   ✓ Status: {reset_resp.status_code}")
    print(f"   ✓ Deleted count: {deleted}")
    print(f"   ✓ Message: {reset_data.get('message')}")
    
    # 4. Check stats after reset
    print("\n4. Checking database stats after reset...")
    stats_after = client.get('/api/stats').json()
    total_after = stats_after.get('total', 0)
    print(f"   ✓ Total jobs in database: {total_after}")
    assert total_after == 0, f"Expected 0 jobs after reset, but got {total_after}"
    
    # 5. Search again to verify new results populate
    print("\n5. Searching again after reset (verify cache cleared)...")
    resp2 = client.post('/api/search/jobs', json={
        'query': 'Python Developer',
        'location': '',
        'limit': 20,
        'no_ai': True,
        'no_cache': True
    })
    count2 = resp2.json().get('count', 0)
    print(f"   ✓ Status: {resp2.status_code}, Jobs returned: {count2}")
    assert count2 > 0, "Expected jobs after reset search (cache should be cleared)"
    
    # 6. Final stats
    print("\n6. Final database stats...")
    stats_final = client.get('/api/stats').json()
    total_final = stats_final.get('total', 0)
    print(f"   ✓ Total jobs in database: {total_final}")
    assert total_final > 0, "Expected jobs in database after final search"
    
    print("\n" + "="*60)
    print("✅ ALL RESET FLOW TESTS PASSED")
    print("="*60)
    print(f"Summary:")
    print(f"  - Before reset: {total_before} jobs")
    print(f"  - Reset deleted: {deleted} jobs")
    print(f"  - After reset (before search): 0 jobs ✓")
    print(f"  - After new search: {count2} jobs ✓")
    print(f"  - Final DB total: {total_final} jobs ✓")
    print()

if __name__ == '__main__':
    test_reset_flow()
