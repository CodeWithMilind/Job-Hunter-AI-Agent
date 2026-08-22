"""Resume storage and lightweight candidate/search-profile extraction."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

MAX_RESUMES = 5
RESUME_STATE = "hirevia_resumes.json"

ROLE_HINTS = {
    "data scientist": ["data scientist", "data science"],
    "machine learning engineer": ["machine learning", "ml engineer"],
    "ai engineer": ["artificial intelligence", "ai engineer", "deep learning"],
    "data analyst": ["data analyst", "business analyst", "analytics"],
    "python developer": ["python developer", "python"],
    "data engineer": ["data engineer", "etl", "data pipeline"],
}
SKILLS = [
    "Python", "SQL", "Java", "JavaScript", "TypeScript", "C++", "R", "Go",
    "Pandas", "NumPy", "Scikit-learn", "TensorFlow", "PyTorch", "Keras",
    "LangChain", "FastAPI", "Django", "Flask", "React", "Node.js", "Spark",
    "Hadoop", "Airflow", "Power BI", "Tableau", "Excel", "MySQL", "PostgreSQL",
    "MongoDB", "Redis", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git",
    "Machine Learning", "Deep Learning", "Data Science", "NLP", "Computer Vision",
]


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception:
            return ""
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            return re.sub(r"<[^>]+>", " ", xml)
        except Exception:
            return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def build_candidate_profile(text: str, name: str) -> dict[str, Any]:
    normalized = text.lower()
    skills = [skill for skill in SKILLS if skill.lower() in normalized]
    roles = [role for role, hints in ROLE_HINTS.items() if any(hint in normalized for hint in hints)]
    experience = re.findall(r"(?:19|20)\d{2}\s*[-–]\s*(?:19|20)?\d{2}|(\d+)\+?\s*years?", normalized)
    education = [line.strip() for line in text.splitlines() if any(word in line.lower() for word in ("b.tech", "b.e", "bachelor", "master", "m.tech", "degree", "university"))][:5]
    certifications = [line.strip() for line in text.splitlines() if "certif" in line.lower()][:5]
    return {
        "name": name,
        "skills": skills,
        "programming_languages": [skill for skill in skills if skill in {"Python", "SQL", "Java", "JavaScript", "TypeScript", "C++", "R", "Go"}],
        "frameworks_libraries": [skill for skill in skills if skill not in {"Python", "SQL", "Java", "JavaScript", "TypeScript", "C++", "R", "Go"}],
        "education": education,
        "certifications": certifications,
        "experience_years": max((int(item) for item in experience if item and item.isdigit()), default=0),
        "likely_roles": roles or ["Software Engineer", "Data Analyst"],
        "keywords": skills[:20],
        "text_excerpt": text[:1000],
    }


def build_search_strategy(profiles: list[dict[str, Any]]) -> dict[str, list[str]]:
    skills = list(dict.fromkeys(skill for profile in profiles for skill in profile.get("skills", [])))[:20]
    roles = list(dict.fromkeys(role.title() for profile in profiles for role in profile.get("likely_roles", [])))
    primary = roles[:10] or ["Software Engineer", "Data Analyst", "Data Scientist"]
    related = [role for role in ["AI Engineer", "Junior Data Scientist", "Machine Learning Engineer", "Data Engineer", "Python Developer", "Data Analyst"] if role not in primary][:10]
    return {
        "primary_roles": primary,
        "related_roles": related,
        "skills": skills,
        "experience_terms": ["Internship", "Intern", "Fresher", "Entry Level", "Graduate", "0 years", "0-1 years", "0-2 years"],
        "locations": ["India", "Pune", "Bengaluru", "Hyderabad", "Mumbai", "Chennai", "Delhi NCR", "Noida", "Gurgaon", "Remote India"],
    }


class ResumeManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.state_path = self.root / RESUME_STATE
        self.upload_dir = self.root / "resume_uploads"
        self.upload_dir.mkdir(exist_ok=True)

    def _read(self) -> list[dict[str, Any]]:
        if not self.state_path.exists():
            return []
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, resumes: list[dict[str, Any]]) -> None:
        self.state_path.write_text(json.dumps(resumes, indent=2), encoding="utf-8")

    def list(self) -> list[dict[str, Any]]:
        return self._read()

    def add(self, filename: str, content: bytes) -> dict[str, Any]:
        resumes = self._read()
        if len(resumes) >= MAX_RESUMES:
            raise ValueError("A maximum of 5 resumes is supported")
        safe_name = Path(filename).name or "resume.txt"
        target = self.upload_dir / f"{len(resumes) + 1}_{safe_name}"
        target.write_bytes(content)
        text = _extract_text(target)
        item = {
            "id": str(len(resumes) + 1),
            "filename": safe_name,
            "profile": build_candidate_profile(text, f"Resume {len(resumes) + 1}"),
        }
        resumes.append(item)
        self._write(resumes)
        self._write_profile(resumes)
        return item

    def remove(self, resume_id: str) -> bool:
        resumes = self._read()
        remaining = [item for item in resumes if item["id"] != resume_id]
        if len(remaining) == len(resumes):
            return False
        self._write(remaining)
        self._write_profile(remaining)
        return True

    def _write_profile(self, resumes: list[dict[str, Any]]) -> None:
        profiles = [item["profile"] for item in resumes]
        combined = {
            "name": "Hirevia Candidate",
            "title": profiles[0].get("likely_roles", ["Job Seeker"])[0] if profiles else "Job Seeker",
            "experience_years": max((p.get("experience_years", 0) for p in profiles), default=0),
            "skills": list(dict.fromkeys(skill for p in profiles for skill in p.get("skills", []))),
            "desired_roles": list(dict.fromkeys(role for p in profiles for role in p.get("likely_roles", []))),
            "remote_ok": True,
        }
        (self.root / "profile.yaml").write_text(json.dumps(combined), encoding="utf-8")

    def strategy(self) -> dict[str, list[str]]:
        return build_search_strategy([item["profile"] for item in self._read()])
