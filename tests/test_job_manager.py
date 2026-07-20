from __future__ import annotations

from src.application.job_manager import JobManager
from src.application.job_repository import JobRepository


def _config():
    return {
        "start_date": "2026-07-08",
        "end_date": "2026-07-08",
        "regions": ["충청북도"],
        "headed": False,
        "resume": False,
        "resume_from_job_id": "",
        "max_issues": None,
        "timeout_seconds": 30,
        "retries": 2,
        "link_delay_seconds": 0.5,
        "debug": False,
    }


def test_records_are_restored_after_server_restart(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    repository = JobRepository(db_path)
    first_manager = JobManager(
        repository, worker_launcher=lambda _job_id: None, recover_on_start=False
    )
    job = first_manager.create_job(_config())
    repository.mark_started(job["job_id"])
    repository.update_job(job["job_id"], processed_links=3, normal_count=2, error_count=1)

    restored_repository = JobRepository(db_path)
    JobManager(
        restored_repository, worker_launcher=lambda _job_id: None, recover_on_start=True
    )
    restored = restored_repository.get_job(job["job_id"])
    assert restored is not None
    assert restored["status"] == "partial_failed"
    assert restored["processed_links"] == 3
    assert "다시 시작" in restored["error_message"]


def test_cancel_requested_state_is_restored_as_cancelled(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(
        repository, worker_launcher=lambda _job_id: None, recover_on_start=False
    )
    job = manager.create_job(_config())
    manager.cancel_job(job["job_id"])
    JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=True)
    assert repository.get_job(job["job_id"])["status"] == "cancelled"

