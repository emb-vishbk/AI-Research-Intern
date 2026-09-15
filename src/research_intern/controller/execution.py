"""Drive one prevalidated, simulated candidate through execution and recording."""

from research_intern.domain.experiments import Candidate, ExperimentRecord, JobRequest, SliceError
from research_intern.evaluation.evaluator import EvaluationError, evaluate, evaluate_baseline
from research_intern.execution.adapter import ExecutionError, Executor, SubmissionError
from research_intern.execution.outputs import OutputError, RunFailedError, collect_outputs
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.paths import child_path


class ExecutionController:
    def __init__(self, ledger: Ledger, executor: Executor):
        if executor.backend != "simulated":
            raise SliceError("This offline slice does not enable live execution adapters")
        self.ledger = ledger
        self.executor = executor

    def import_baseline(self, git_commit: str) -> ExperimentRecord:
        request = JobRequest("EXP-000", None, git_commit)
        result = collect_outputs(self.ledger.outputs("EXP-000"), self.ledger.rules, request, self.ledger.output_paths)
        evaluation = evaluate_baseline(self.ledger.rules, result)
        return self.ledger.import_baseline(git_commit, result, evaluation)

    def start(self, candidate: Candidate) -> ExperimentRecord:
        return self.resume_submission(self.ledger.reserve(candidate).experiment_id)

    def resume_submission(self, experiment_id: str) -> ExperimentRecord:
        record = self.ledger.get(experiment_id)
        if record.state == "SUBMITTING":
            raise SliceError("Submission outcome is uncertain; reconcile its job ID before continuing")
        if record.state != "PREPARED":
            return record
        diff = child_path(self.ledger.directory(experiment_id), "diff.patch")
        if diff.exists():
            if diff.read_text(encoding="utf-8") != record.diff:
                raise SliceError("The persisted candidate diff has changed")
        else:
            with diff.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(record.diff)
        request = JobRequest(record.experiment_id, record.parent_experiment, record.git_commit)
        self.ledger.begin_submission(experiment_id)
        try:
            job_id = self.executor.submit_job(request)
        except SubmissionError as exc:
            return self.ledger.record_failure(experiment_id, "SUBMISSION_FAILED", str(exc))
        # This transaction commits before the first status request. Unknown
        # submission outcomes remain SUBMITTING; resume never resubmits them.
        self.ledger.record_job(experiment_id, job_id)
        return self.ledger.get(experiment_id)

    def advance(self, experiment_id: str) -> ExperimentRecord:
        """One polling step; no busy loop, model session, or implicit resubmission."""
        record = self.ledger.get(experiment_id)
        if record.terminal:
            return record
        if record.state not in ("SUBMITTED", "RUNNING") or record.job_id is None:
            raise SliceError("The experiment needs a persisted job ID before polling")
        status = record.job_status
        if status not in ("completed", "failed", "cancelled"):
            # Transient status errors propagate, retaining the persisted job for retry.
            status = self.executor.get_status(record.job_id)
            self.ledger.record_status(experiment_id, status)
        if status in ("queued", "running"):
            return self.ledger.get(experiment_id)

        outputs = self.ledger.outputs(experiment_id)
        try:
            if not outputs.exists():
                self.executor.download_outputs(record.job_id, outputs)
        except ExecutionError as exc:
            if status in ("failed", "cancelled"):
                return self.ledger.record_failure(experiment_id, "EXECUTION_FAILED",
                                                  f"Job {status}; output retrieval also failed: {exc}")
            # A completed job with a temporary download failure can be resumed.
            raise
        if status in ("failed", "cancelled"):
            return self.ledger.record_failure(experiment_id, "EXECUTION_FAILED", f"Job {status}")
        request = JobRequest(record.experiment_id, record.parent_experiment, record.git_commit)
        try:
            result = collect_outputs(outputs, self.ledger.rules, request, self.ledger.output_paths)
        except RunFailedError as exc:
            return self.ledger.record_failure(experiment_id, "EXECUTION_FAILED", str(exc))
        except OutputError as exc:
            return self.ledger.record_failure(experiment_id, "OUTPUT_INVALID", str(exc))
        if record.parent_experiment is None:
            raise SliceError("A candidate needs a persisted selected parent")
        parent = self.ledger.get(record.parent_experiment)
        best = self.ledger.best()
        if parent.evaluation is None or best.evaluation is None:
            raise SliceError("The parent and current best need persisted evaluations")
        try:
            evaluation = evaluate(self.ledger.rules, result,
                                  parent_score=parent.evaluation.score,
                                  best_score=best.evaluation.score)
        except EvaluationError as exc:
            return self.ledger.record_failure(experiment_id, "EVALUATION_INVALID", str(exc))
        return self.ledger.record_result(experiment_id, result, evaluation)
