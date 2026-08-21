from hirevia.eligibility import INDIA_ELIGIBLE, INDIA_NOT_ELIGIBLE, UNKNOWN, classify_india_eligibility


def test_india_location_is_eligible():
    assert classify_india_eligibility(location="Bengaluru, India") == INDIA_ELIGIBLE


def test_global_and_anywhere_remote_are_eligible():
    assert classify_india_eligibility(location="Worldwide remote") == INDIA_ELIGIBLE
    assert classify_india_eligibility(description="Work from anywhere in the world") == INDIA_ELIGIBLE


def test_explicit_india_overrides_other_country_mentions():
    assert classify_india_eligibility(description="Open to candidates in the US or India") == INDIA_ELIGIBLE


def test_country_restriction_fields_are_not_eligible():
    assert classify_india_eligibility(location_restrictions=["United States"], remote=True) == INDIA_NOT_ELIGIBLE
    assert classify_india_eligibility(country="Canada") == INDIA_NOT_ELIGIBLE


def test_description_requires_explicit_restriction_language():
    assert classify_india_eligibility(description="Applicants must be authorized to work in the UK") == INDIA_NOT_ELIGIBLE
    assert classify_india_eligibility(description="Our US team collaborates with customers") == UNKNOWN


def test_india_timezone_is_eligible_and_remote_alone_is_unknown():
    assert classify_india_eligibility(timezone="IST") == INDIA_ELIGIBLE
    assert classify_india_eligibility(timezone="America/New_York") == INDIA_NOT_ELIGIBLE
    assert classify_india_eligibility(remote=True) == UNKNOWN
