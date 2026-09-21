"""
Core normalized data model for Beyond The Pitch.

Every ingestion adapter (zip upload, manual form, GitHub, GitLab, etc.)
must produce a SubmissionBundle. The scoring engine downstream never
knows or cares where the data came from — this is the contract that
keeps ingestion and evaluation decoupled.
"""

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class CodeFile:
    """A single source file pulled from the submission, trimmed to a
    reasonable size before it ever reaches an LLM prompt."""
    path: str                # relative path within the submission
    content: str             # file contents (may be truncated)
    language: Optional[str] = None
    truncated: bool = False


@dataclass
class DemoAsset:
    """Whatever evidence of a working demo the team provided."""
    kind: str                 # "video_url" | "live_url" | "screenshots" | "none"
    value: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class SubmissionBundle:
    """
    The normalized, source-agnostic representation of one hackathon
    submission. This is the ONLY object the scoring engine consumes.
    """
    team_name: str
    project_title: str

    # Free-text pitch content, usually copy-pasted from a submission form
    problem_statement: str = ""
    business_impact_pitch: str = ""
    tech_stack: str = ""

    # Extracted artifacts
    readme_text: str = ""
    code_files: list[CodeFile] = field(default_factory=list)
    demo: DemoAsset = field(default_factory=lambda: DemoAsset(kind="none"))

    # Provenance — useful for debugging/audit, never used in scoring logic
    source_adapter: str = "unknown"
    source_reference: Optional[str] = None  # e.g. path, URL, form ID

    def code_char_count(self) -> int:
        return sum(len(f.content) for f in self.code_files)

    def has_code(self) -> bool:
        return len(self.code_files) > 0

    def to_prompt_context(self, max_code_chars: int = 20000) -> str:
        """
        Builds a single text blob safe to drop into an LLM prompt.
        Caps code volume so a 50-file monorepo doesn't blow the context
        window — sampling strategy lives in the adapter, this just guards
        the final cut.
        """
        parts = [
            f"TEAM: {self.team_name}",
            f"PROJECT: {self.project_title}",
            f"\n--- PROBLEM STATEMENT ---\n{self.problem_statement or '(not provided)'}",
            f"\n--- BUSINESS IMPACT PITCH ---\n{self.business_impact_pitch or '(not provided)'}",
            f"\n--- TECH STACK ---\n{self.tech_stack or '(not provided)'}",
            f"\n--- README ---\n{self.readme_text or '(no README found)'}",
            f"\n--- DEMO EVIDENCE ---\nKind: {self.demo.kind}\nValue: {self.demo.value or 'none'}\nNotes: {self.demo.notes or ''}",
        ]

        code_blob = []
        running_total = 0
        for f in self.code_files:
            chunk = f"\n### FILE: {f.path}\n{f.content}"
            if running_total + len(chunk) > max_code_chars:
                code_blob.append(f"\n[...remaining {len(self.code_files) - len(code_blob)} files truncated for length...]")
                break
            code_blob.append(chunk)
            running_total += len(chunk)

        parts.append(f"\n--- CODE SAMPLE ({len(self.code_files)} files total) ---" + "".join(code_blob))
        return "\n".join(parts)
