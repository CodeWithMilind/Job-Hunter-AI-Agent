"""Data models for Hirevia."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml
from hirevia.eligibility import UNKNOWN, classify_india_eligibility


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    salary: str = ""
    source: str = ""
    source_type: str = "Custom"
    original_url: str = ""
    source_metadata: Dict[str, str] = field(default_factory=dict)
    remote: bool = False
    tags: List[str] = field(default_factory=list)
    posted: str = ""
    # AI ratings
    score: int = 0
    rating: str = ""
    reasoning: str = ""
    skills_match: int = 0
    experience_fit: int = 0
    salary_fit: int = 0
    remote_fit: int = 0
    country: str = ""
    location_restrictions: List[str] = field(default_factory=list)
    timezone: str = ""
    india_eligibility: str = UNKNOWN

    def __post_init__(self):
        """Derive location eligibility from source data, never an LLM."""
        self.india_eligibility = classify_india_eligibility(
            location=self.location,
            location_restrictions=self.location_restrictions,
            country=self.country,
            timezone=self.timezone,
            description=self.description,
            remote=self.remote,
        )


@dataclass
class Profile:
    name: str = "User"
    title: str = ""
    experience_years: int = 0
    skills: List[str] = field(default_factory=list)
    desired_roles: List[str] = field(default_factory=list)
    salary_min: int = 0
    salary_max: int = 0
    location_preference: str = ""
    remote_ok: bool = True
    industries: List[str] = field(default_factory=list)
    target_roles: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    experience: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    telegram: Dict[str, object] = field(default_factory=dict)
    settings: Dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str) -> "Profile":
        """Load a Profile from a YAML file.

        Raises FileNotFoundError if the file does not exist, or ValueError
        if the YAML is invalid.
        """
        with open(path) as f:
            data = yaml.safe_load(f)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"Profile YAML must be a mapping, got {type(data).__name__}")
        known = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in known}
        if not filtered.get("desired_roles") and data.get("target_roles"):
            filtered["desired_roles"] = data["target_roles"]
        if not filtered.get("skills") and data.get("keywords"):
            filtered["skills"] = data["keywords"]
        unknown = set(data.keys()) - known
        if unknown:
            # Warn but don't fail — allow forward-compatible profiles
            import warnings
            warnings.warn(f"Unknown profile fields ignored: {unknown}")
        return cls(**filtered)
