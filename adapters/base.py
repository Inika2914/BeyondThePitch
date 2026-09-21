"""
Adapter contract. Every ingestion source implements this interface and
returns a SubmissionBundle. Adding a new platform (GitLab, Devpost export,
etc.) never requires touching the scoring engine.
"""

from abc import ABC, abstractmethod
from models.submission import SubmissionBundle


class BaseAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def ingest(self, **kwargs) -> SubmissionBundle:
        """Produce a normalized SubmissionBundle from whatever this
        adapter's source format is."""
        raise NotImplementedError
