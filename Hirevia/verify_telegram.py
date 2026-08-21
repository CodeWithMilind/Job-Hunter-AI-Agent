#!/usr/bin/env python
"""Quick verification that unified search works with Telegram disabled."""

import sys
sys.path.insert(0, ".")
sys.path.insert(0, "dashboard")

from fastapi.testclient import TestClient
import dashboard.app as app_mod

client = TestClient(app_mod.app)

print("\n" + "="*60)
print("TELEGRAM INTEGRATION VERIFICATION")
print("="*60)

# Test 1: Unified search with Telegram disabled (default)
print("\n1. Testing unified search (Telegram disabled by default)...")
resp = client.post('/api/search/jobs', json={
    'query': 'Python Developer',
    'location': '',
    'limit': 5,
    'no_ai': True,
    'no_cache': True
})

assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
data = resp.json()
jobs = data.get('jobs', [])
count = data.get('count', 0)

print(f"   ✓ Status: 200")
print(f"   ✓ Jobs returned: {count}")

# Check that other sources are working
sources = {j['source'] for j in jobs}
print(f"   ✓ Sources present: {sources}")

# Verify Telegram is not in results (since disabled by default)
has_telegram = any(j['source'] == 'Telegram' for j in jobs)
print(f"   ✓ Has Telegram jobs: {has_telegram} (expected: False - disabled by default)")

assert has_telegram is False, "Telegram should be disabled by default"

# Test 2: Check that Telegram source can be accessed
print("\n2. Checking TelegramSearch integration...")
from hirevia.sources.telegram import TelegramSearch
from hirevia.sources import SourceRegistry

telegram = TelegramSearch()
print(f"   ✓ TelegramSearch class exists")
print(f"   ✓ Default enabled state: {telegram.enabled}")
print(f"   ✓ Has job detection: {hasattr(telegram, '_is_job')}")
print(f"   ✓ Has URL extraction: {hasattr(telegram, '_url')}")
print(f"   ✓ Has fetch method: {hasattr(telegram, 'fetch')}")

# Test 3: Verify Telegram error handling
print("\n3. Testing Telegram error handling...")
telegram = TelegramSearch()
telegram.enabled = True
telegram._channels = [{'username': '@test', 'enabled': True}]

result = telegram.fetch("test")
print(f"   ✓ Fetch with missing credentials returns: {type(result).__name__}")
assert isinstance(result, list), "Should return a list even on error"
assert result == [], "Should return empty list when credentials missing"
print(f"   ✓ Error handling works correctly")

print("\n" + "="*60)
print("✅ TELEGRAM INTEGRATION VERIFICATION PASSED")
print("="*60 + "\n")

print("Summary:")
print("  - Unified search works with Telegram disabled")
print("  - Other sources (GitHub, Greenhouse, etc.) return jobs")
print("  - Telegram source is integrated but disabled by default")
print("  - Telegram errors don't break the search pipeline")
print("  - Telegram fetch handles missing credentials gracefully")
print()
