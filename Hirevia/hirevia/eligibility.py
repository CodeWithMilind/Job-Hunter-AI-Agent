"""Deterministic location eligibility checks for Hirevia jobs."""

import re
from typing import Iterable

INDIA_ELIGIBLE = "INDIA_ELIGIBLE"
INDIA_NOT_ELIGIBLE = "INDIA_NOT_ELIGIBLE"
UNKNOWN = "UNKNOWN"


def _text(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _has(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


INDIA_PATTERNS = (r"\bindia\b", r"\bindian\b", r"\bpan\s*india\b", r"\b(?:pune|mumbai|bangalore|bengaluru|hyderabad|chennai|delhi(?:\s*ncr)?|noida|gurgaon|gurugram|ahmedabad|kolkata|jaipur)\b", r"\bist\b", r"india standard time")
GLOBAL_PATTERNS = (r"\bworldwide\b", r"\bglobal(?:ly)?\b", r"\banywhere\b", r"\bwork from anywhere\b", r"\bremote\s*[- ]?first\b")
RESTRICTED_COUNTRY_PATTERNS = (r"\b(?:united states|u\.?s\.?a?|us)\b", r"\bcanada\b", r"\b(?:united kingdom|u\.?k\.?)\b", r"\b(?:european union|eu)\b", r"\baustralia\b", r"\bnew zealand\b", r"\bgermany\b", r"\bfrance\b", r"\bnew york\b", r"\blos angeles\b", r"\bsan francisco\b", r"\bchicago\b")
RESTRICTED_TIMEZONE_PATTERNS = (r"\bamerica/(?:new_york|chicago|denver|los_angeles)\b", r"\b(?:us|u\.?s\.?)\s*(?:time\s*zone|timezone)s?\b")
RESTRICTION_WORDS = r"(?:only|exclusive(?:ly)?|must|require(?:d)?|restricted|eligible|authorized|based|resid(?:e|ency)|within)"


def classify_india_eligibility(*, location: str = "", location_restrictions: object = "", country: str = "", timezone: str = "", description: str = "", remote: bool = False) -> str:
    """Classify whether a job can be held from India without using an LLM.

    Explicit India and global/anywhere signals take precedence over otherwise
    restrictive text (for example, "US or India"). A remote flag alone is
    deliberately insufficient: remote jobs can still be country-restricted.
    """
    location_text = _text(location)
    restrictions_text = _text(location_restrictions)
    country_text = _text(country)
    timezone_text = _text(timezone)
    description_text = _text(description)
    all_text = " ".join((location_text, restrictions_text, country_text, timezone_text, description_text))

    # A company description mentioning India is not evidence that this role
    # can be performed from India. Structured location data decides first.
    location_signals = " ".join((location_text, restrictions_text, country_text, timezone_text))
    if _has(location_signals, INDIA_PATTERNS) or _has(location_signals, GLOBAL_PATTERNS):
        return INDIA_ELIGIBLE

    # Dedicated country/restriction fields are authoritative even without a
    # qualifier (e.g. Himalayas: ["United States"]).
    structured_text = " ".join((location_text, restrictions_text, country_text, timezone_text))
    if _has(structured_text, RESTRICTED_COUNTRY_PATTERNS):
        return INDIA_NOT_ELIGIBLE
    if _has(timezone_text, RESTRICTED_TIMEZONE_PATTERNS):
        return INDIA_NOT_ELIGIBLE

    # Descriptions can mention offices, so require an applicant/location
    # statement rather than accepting incidental country mentions.
    if _has(description_text, (r"(?:applicants?|candidates?|open|remote|work)\b.{0,60}\b(?:in|from|across)\s+india\b", r"\b(?:india|us|usa|united states)\b.{0,25}\b(?:or|and)\b.{0,25}\bindia\b", r"\b(?:remote|work)\b.{0,45}\b(?:worldwide|anywhere|global)\b", r"\bwork from anywhere in the world\b")):
        return INDIA_ELIGIBLE
    for country_pattern in RESTRICTED_COUNTRY_PATTERNS:
        if re.search(rf"{RESTRICTION_WORDS}.{{0,45}}{country_pattern}|{country_pattern}.{{0,45}}{RESTRICTION_WORDS}", description_text, re.IGNORECASE):
            return INDIA_NOT_ELIGIBLE
    return UNKNOWN
