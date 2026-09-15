"""The small execution seam to be implemented by Azure ML later."""

from pathlib import Path
from typing import Literal, Protocol

from research_intern.domain.experiments import JobRequest, SliceError

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


class SubmissionError(SliceError):
    """Submission was definitively rejected; no remote job was created.

    An ambiguous timeout must propagate instead, leaving SUBMITTING for explicit
    reconciliation. It must not be treated as permission to submit again.
    """


class ExecutionError(SliceError):
    """Status lookup, cancellation, or output retrieval failed."""


class Executor(Protocol):
    backend: str

    def submit_job(self, request: JobRequest) -> str: ...

    def get_status(self, job_id: str) -> JobStatus: ...

    def download_outputs(self, job_id: str, destination: Path) -> None:
        """Publish outputs atomically; never overwrite an existing destination."""
        ...

    def cancel_job(self, job_id: str) -> None: ...
