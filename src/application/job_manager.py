from __future__ import annotations

import multiprocessing
import os
import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.config import OUTPUT_DIR, WORK_DIR
from src.logging_utils import sanitize
from src.regions import resume_checkpoint_path

from .audit_service import AuditRequest, AuditRunResult, AuditService
from .job_repository import (
    ACTIVE_STATUSES,
    CANCELLATION_STATUSES,
    TERMINAL_STATUSES,
    ActiveJobExistsError,
    JobRepository,
    now_iso,
)
from .progress import CancellationToken, ProgressReporter


class SqliteProgressReporter(ProgressReporter):
    PROGRESS_FIELDS = {
        "current_date", "current_region", "current_issue", "current_issue_order",
        "current_issue_total", "current_region_completed_issues",
        "current_region_total_issues", "current_issue_processed_articles",
        "current_issue_total_articles", "current_publisher", "current_article_title",
        "total_regions", "completed_regions",
        "total_region_units", "known_links", "processed_links", "normal_count",
        "error_count", "excel_path", "error_message",
    }

    def __init__(self, repository: JobRepository, job_id: str):
        self.repository = repository
        self.job_id = job_id

    def emit(self, event: str, message: str = "", **data: Any) -> None:
        if event == "cancel_acknowledged":
            self.repository.acknowledge_cancel(self.job_id)
        fields = {key: value for key, value in data.items() if key in self.PROGRESS_FIELDS}
        stamp = now_iso()
        if event in {"link_completed", "issue_completed", "region_completed"}:
            fields["last_progress_at"] = stamp
        operation = data.get("current_operation")
        if operation is not None:
            fields["current_operation"] = operation
            fields["operation_started_at"] = data.get(
                "operation_started_at", stamp if operation else ""
            )
        if "browser_state" in data:
            fields["browser_state"] = data["browser_state"]
        if "browser_restart_count" in data:
            fields["browser_restart_count"] = data["browser_restart_count"]
        if fields:
            self.repository.update_job(self.job_id, **fields)
        if message:
            level = "warning" if event in {"cancel_acknowledged"} else "info"
            self.repository.append_log(self.job_id, message, level)


class SqliteCancellationToken(CancellationToken):
    def __init__(self, repository: JobRepository, job_id: str):
        self.repository = repository
        self.job_id = job_id

    def is_cancel_requested(self) -> bool:
        return self.repository.is_cancel_requested(self.job_id)


def _finish_worker_job(repository: JobRepository, job_id: str, result: AuditRunResult) -> None:
    repository.replace_results(job_id, result.rows)
    normal_count = sum(row.verdict == "정상" for row in result.rows)
    repository.update_job(
        job_id,
        status=result.status,
        ended_at=result.ended_at.isoformat(timespec="seconds"),
        completed_regions=result.completed_region_units,
        processed_links=len(result.rows),
        normal_count=normal_count,
        error_count=len(result.rows) - normal_count,
        excel_path=str(result.excel_path or ""),
        checkpoint_path=str(result.checkpoint_path),
        log_path=str(result.log_path),
        error_message=result.error_message,
        termination_reason=result.status,
        **(
            {"current_operation": "", "operation_started_at": ""}
            if result.status == "completed"
            else {}
        ),
    )
    repository.refresh_resume_metadata(job_id)


def _heartbeat_loop(repository: JobRepository, job_id: str, stop: threading.Event) -> None:
    while not stop.wait(5.0):
        if not repository.touch_heartbeat(job_id):
            return


def run_audit_job_worker(db_path: str, job_id: str) -> None:
    """spawn worker 진입점. CLI 문자열이 아니라 공통 AuditService를 직접 호출한다."""
    repository = JobRepository(Path(db_path))
    job = repository.get_job(job_id)
    if not job:
        return
    if not repository.mark_started(job_id, os.getpid()) and not repository.is_cancel_requested(job_id):
        return
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(repository, job_id, heartbeat_stop),
        name=f"bigkinds-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat.start()
    repository.append_log(job_id, "점검 작업을 준비하고 있습니다.")
    request = AuditRequest(
        start_date=job["start_date"],
        end_date=job["end_date"],
        regions=job["regions"],
        headed=job["headed"],
        resume=job["resume"],
        max_issues=job["max_issues"],
        timeout_seconds=job["timeout_seconds"],
        retries=job["retries"],
        link_delay_seconds=job["link_delay_seconds"],
        debug=job["debug"],
        checkpoint_path=Path(job["checkpoint_path"]),
        output_tag=f"job_{job_id[:8]}",
    )
    try:
        result = AuditService().run(
            request,
            reporter=SqliteProgressReporter(repository, job_id),
            cancellation_token=SqliteCancellationToken(repository, job_id),
        )
        _finish_worker_job(repository, job_id, result)
    except BaseException as exc:
        message = sanitize(exc)
        repository.update_job(
            job_id,
            status="failed",
            ended_at=now_iso(),
            error_message=message,
            termination_reason="worker_exception",
        )
        repository.append_log(job_id, f"작업을 완료하지 못했습니다: {message}", "error")
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)


class JobManager:
    def __init__(
        self,
        repository: JobRepository,
        *,
        worker_launcher: Callable[[str], None] | None = None,
        recover_on_start: bool = True,
        cancel_grace_seconds: float = 10.0,
        terminate_grace_seconds: float = 5.0,
    ):
        self.repository = repository
        self._processes: dict[str, multiprocessing.Process] = {}
        self._worker_launcher = worker_launcher
        self.cancel_grace_seconds = cancel_grace_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self._cancel_watchdogs: set[str] = set()
        self._cancel_watchdogs_lock = threading.Lock()
        if recover_on_start:
            self.repository.recover_interrupted_jobs()

    def create_job(self, config: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid4().hex
        resume_from_job_id = str(config.get("resume_from_job_id") or "")
        resume = bool(config.get("resume"))
        if resume_from_job_id:
            source = self.repository.get_job(resume_from_job_id)
            if not source:
                raise ValueError("재개할 이전 작업을 찾을 수 없습니다.")
            if source["status"] not in TERMINAL_STATUSES:
                raise ValueError("아직 실행 중인 작업에서는 재개할 수 없습니다.")
            source = self.repository.refresh_resume_metadata(resume_from_job_id)
            if not source["manual_resume_available"]:
                raise ValueError(source["manual_resume_reason"] or "이 작업은 재개할 수 없습니다.")
            for key in ("start_date", "end_date", "regions"):
                if source[key] != config[key]:
                    raise ValueError("재개 작업의 날짜와 지역은 이전 작업과 같아야 합니다.")
            checkpoint_path = Path(source["checkpoint_path"])
            if not checkpoint_path.is_file():
                raise ValueError("재개할 체크포인트 파일이 없습니다.")
            resume = True
        elif resume:
            checkpoint_path = resume_checkpoint_path(
                WORK_DIR, config["start_date"], config["end_date"], config["regions"], True
            )
            if not checkpoint_path.is_file():
                raise ValueError("조건에 맞는 기존 체크포인트 파일이 없습니다.")
        else:
            checkpoint_path = WORK_DIR / "web_jobs" / f"{job_id}.jsonl"

        stored = {
            **config,
            "resume": resume,
            "resume_from_job_id": resume_from_job_id,
        }
        job = self.repository.create_job(job_id, stored, checkpoint_path)
        try:
            self._launch(job_id)
        except Exception as exc:
            self.repository.update_job(
                job_id, status="failed", ended_at=now_iso(), error_message=sanitize(exc)
            )
            self.repository.append_log(job_id, "작업 프로세스를 시작하지 못했습니다.", "error")
        return self.repository.get_job(job_id) or job

    def _launch(self, job_id: str) -> None:
        if self._worker_launcher is not None:
            self._worker_launcher(job_id)
            return
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=run_audit_job_worker,
            args=(str(self.repository.db_path), job_id),
            name=f"bigkinds-audit-{job_id[:8]}",
        )
        process.start()
        self._processes[job_id] = process
        if process.pid is not None:
            self.repository.record_worker_spawned(job_id, process.pid)
        self.repository.append_log(
            job_id,
            f"작업 프로세스를 시작했습니다. pid={process.pid if process.pid is not None else 'unknown'}",
        )
        threading.Thread(
            target=self._watch_process,
            args=(job_id, process),
            name=f"bigkinds-watch-{job_id[:8]}",
            daemon=True,
        ).start()

    def _watch_process(self, job_id: str, process: multiprocessing.Process) -> None:
        process.join()
        try:
            exit_code = process.exitcode
            job = self.repository.get_job(job_id)
            if not job:
                return
            self.repository.update_job(job_id, worker_exit_code=exit_code)
            self.repository.append_log(
                job_id,
                f"작업 프로세스가 종료되었습니다. pid={process.pid}, exit_code={exit_code}",
                "info" if exit_code == 0 else "error",
            )
            job = self.repository.get_job(job_id)
            if not job or job["status"] not in ACTIVE_STATUSES:
                return
            if job["status"] in CANCELLATION_STATUSES:
                status = "cancelled"
                message = "작업 프로세스가 중단되었습니다."
                termination_reason = (
                    "force_terminated"
                    if job["status"] == "force_terminating"
                    else "cancel_requested"
                )
            elif job["processed_links"]:
                status = "partial_failed"
                message = "작업 프로세스가 예기치 않게 종료되었습니다."
                termination_reason = "worker_exited_unexpectedly"
            else:
                status = "failed"
                message = "작업 프로세스가 예기치 않게 종료되었습니다."
                termination_reason = "worker_exited_unexpectedly"
            finalized = self.repository.finalize_interrupted_job(
                job_id,
                status=status,
                error_message=message,
                termination_reason=termination_reason,
                worker_exit_code=exit_code,
            )
            self.repository.append_log(
                job_id,
                (
                    f"{message} {finalized['manual_resume_reason']}"
                    if finalized["manual_resume_reason"] else message
                ),
                "error",
            )
        finally:
            if self._processes.get(job_id) is process:
                self._processes.pop(job_id, None)

    def _start_cancel_watchdog(
        self, job_id: str, process: multiprocessing.Process
    ) -> None:
        with self._cancel_watchdogs_lock:
            if job_id in self._cancel_watchdogs:
                return
            self._cancel_watchdogs.add(job_id)

        def enforce() -> None:
            try:
                self._enforce_cancel_deadline(job_id, process)
            finally:
                with self._cancel_watchdogs_lock:
                    self._cancel_watchdogs.discard(job_id)

        threading.Thread(
            target=enforce,
            name=f"bigkinds-cancel-{job_id[:8]}",
            daemon=True,
        ).start()

    def _is_recorded_worker(
        self, job_id: str, process: multiprocessing.Process, *, require_tracked: bool = True
    ) -> bool:
        if require_tracked and self._processes.get(job_id) is not process:
            return False
        job = self.repository.get_job(job_id)
        return bool(
            job
            and process.pid is not None
            and job["worker_pid"] == process.pid
            and job["status"] in CANCELLATION_STATUSES
        )

    def _enforce_cancel_deadline(
        self, job_id: str, process: multiprocessing.Process
    ) -> None:
        process.join(timeout=self.cancel_grace_seconds)
        if not process.is_alive() or not self._is_recorded_worker(job_id, process):
            return
        if not self.repository.acknowledge_cancel(job_id):
            return
        if not self.repository.mark_force_terminating(job_id, process.pid):
            return
        self.repository.append_log(
            job_id,
            f"중단 제한시간을 초과하여 작업 프로세스를 종료합니다. pid={process.pid}",
            "warning",
        )
        process.terminate()
        process.join(timeout=self.terminate_grace_seconds)
        if not process.is_alive() or not self._is_recorded_worker(job_id, process):
            return
        self.repository.append_log(
            job_id,
            f"작업 프로세스가 종료되지 않아 최종 종료합니다. pid={process.pid}",
            "error",
        )
        process.kill()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] == "cancelled":
            return job
        if job["status"] in TERMINAL_STATUSES:
            raise RuntimeError("이미 종료된 작업입니다.")
        if not self.repository.request_cancel(job_id, requested_by="user"):
            raise RuntimeError("중단 요청을 적용할 수 없습니다.")
        self.repository.append_log(job_id, "중단 요청을 보냈습니다. 현재 링크를 정리한 뒤 종료합니다.", "warning")
        process = self._processes.get(job_id)
        if process is not None and process.is_alive():
            self._start_cancel_watchdog(job_id, process)
        return self.repository.get_job(job_id)  # type: ignore[return-value]

    def shutdown(self) -> None:
        for job_id, process in list(self._processes.items()):
            if process.is_alive():
                self.repository.request_cancel(job_id, requested_by="server_shutdown")
                self._enforce_cancel_deadline(job_id, process)
            if not process.is_alive() and self._processes.get(job_id) is process:
                self._processes.pop(job_id, None)
