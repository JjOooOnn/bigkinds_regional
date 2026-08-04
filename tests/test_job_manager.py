from __future__ import annotations

import json
import sqlite3

import pytest

from src.application.job_manager import JobManager, SqliteProgressReporter, _heartbeat_loop
from src.application.job_repository import (
    CANCELLATION_STATUSES,
    JobRepository,
    OBSERVABILITY_COLUMNS,
)


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


def _remove_observability_columns(db_path):
    with sqlite3.connect(db_path) as connection:
        for column in OBSERVABILITY_COLUMNS:
            connection.execute(f"ALTER TABLE jobs DROP COLUMN {column}")


def test_records_are_restored_after_server_restart(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    repository = JobRepository(db_path)
    first_manager = JobManager(
        repository, worker_launcher=lambda _job_id: None, recover_on_start=False
    )
    job = first_manager.create_job(_config())
    repository.mark_started(job["job_id"], 4321)
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
    assert restored["worker_pid"] == 4321
    assert restored["attempt_number"] == 1
    assert restored["heartbeat_at"]
    assert restored["termination_reason"] == "server_restart"


@pytest.mark.parametrize("cancel_status", CANCELLATION_STATUSES)
def test_cancel_requested_state_is_restored_as_cancelled(tmp_path, cancel_status):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(
        repository, worker_launcher=lambda _job_id: None, recover_on_start=False
    )
    job = manager.create_job(_config())
    manager.cancel_job(job["job_id"])
    repository.update_job(job["job_id"], status=cancel_status)
    JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=True)
    assert repository.get_job(job["job_id"])["status"] == "cancelled"


def test_observability_fields_distinguish_heartbeat_progress_and_cancel_source(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=False)
    job = manager.create_job(_config())
    job_id = job["job_id"]
    assert repository.mark_started(job_id, 9876)

    started = repository.get_job(job_id)
    assert started["worker_pid"] == 9876
    assert started["attempt_number"] == 1
    assert started["heartbeat_at"]
    assert started["last_progress_at"] == ""

    reporter = SqliteProgressReporter(repository, job_id)
    reporter.emit(
        "region_started",
        current_region="부산광역시",
        current_region_completed_issues=0,
        current_region_total_issues=None,
        current_issue_processed_articles=0,
        current_issue_total_articles=None,
        current_publisher="",
        current_article_title="",
    )
    reporter.emit(
        "issue_started",
        current_issue="테스트 이슈",
        current_issue_order=1,
        current_issue_total=3,
        current_region_completed_issues=0,
        current_region_total_issues=3,
        current_issue_processed_articles=0,
        current_issue_total_articles=None,
    )
    reporter.emit(
        "link_started",
        current_issue_processed_articles=1,
        current_issue_total_articles=4,
        current_publisher="테스트일보",
        current_article_title="진행 중인 기사",
        current_operation="link_check:test",
    )
    article_progress = repository.get_job(job_id)
    assert article_progress["current_region_completed_issues"] == 0
    assert article_progress["current_region_total_issues"] == 3
    assert article_progress["current_issue_processed_articles"] == 1
    assert article_progress["current_issue_total_articles"] == 4
    assert article_progress["current_publisher"] == "테스트일보"
    assert article_progress["current_article_title"] == "진행 중인 기사"

    reporter.emit("link_started", current_operation="link_check:test")
    in_progress = repository.get_job(job_id)
    assert in_progress["current_operation"] == "link_check:test"
    assert in_progress["operation_started_at"]
    assert in_progress["last_progress_at"] == ""

    reporter.emit("link_completed", current_operation="")
    progressed = repository.get_job(job_id)
    assert progressed["last_progress_at"]
    assert progressed["current_operation"] == ""
    assert progressed["operation_started_at"] == ""

    assert repository.request_cancel(job_id, requested_by="test_user")
    cancelled = repository.get_job(job_id)
    assert cancelled["cancel_requested_at"]
    assert cancelled["cancel_requested_by"] == "test_user"
    reporter.emit("cancel_acknowledged", "cancel acknowledged")
    assert repository.get_job(job_id)["status"] == "cancelling"


def test_cancel_state_transitions_are_monotonic_idempotent_and_pid_scoped(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=False)
    job = manager.create_job(_config())
    job_id = job["job_id"]
    assert repository.mark_started(job_id, 9876)

    assert repository.request_cancel(job_id, requested_by="first_user")
    requested = repository.get_job(job_id)
    assert repository.request_cancel(job_id, requested_by="second_user")
    repeated = repository.get_job(job_id)
    assert repeated["cancel_requested_at"] == requested["cancel_requested_at"]
    assert repeated["cancel_requested_by"] == "first_user"

    assert repository.acknowledge_cancel(job_id)
    assert repository.acknowledge_cancel(job_id)
    assert repository.get_job(job_id)["status"] == "cancelling"
    assert not repository.mark_force_terminating(job_id, 1111)
    assert repository.mark_force_terminating(job_id, 9876)
    assert repository.request_cancel(job_id, requested_by="late_user")
    forced = repository.get_job(job_id)
    assert forced["status"] == "force_terminating"
    assert forced["cancel_requested_by"] == "first_user"
    assert repository.is_cancel_requested(job_id)


def test_heartbeat_loop_updates_until_stopped():
    class Repository:
        touches = 0

        def touch_heartbeat(self, job_id):
            assert job_id == "job-id"
            self.touches += 1
            return True

    class Stop:
        waits = 0

        def wait(self, timeout):
            assert timeout == 5.0
            self.waits += 1
            return self.waits > 1

    repository = Repository()
    _heartbeat_loop(repository, "job-id", Stop())
    assert repository.touches == 1


def test_worker_exit_code_and_reason_are_recorded(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    manager = JobManager(repository, worker_launcher=lambda _job_id: None, recover_on_start=False)
    job = manager.create_job(_config())
    job_id = job["job_id"]
    repository.mark_started(job_id, 2468)

    class Process:
        pid = 2468
        exitcode = -9

        @staticmethod
        def join():
            return None

    manager._watch_process(job_id, Process())
    stopped = repository.get_job(job_id)
    assert stopped["status"] == "failed"
    assert stopped["worker_exit_code"] == -9
    assert stopped["termination_reason"] == "worker_exited_unexpectedly"
    assert any("exit_code=-9" in entry["message"] for entry in repository.get_logs(job_id))


def test_existing_schema_is_backed_up_before_additive_migration(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    repository = JobRepository(db_path)
    job = repository.create_job("legacy-job", _config(), tmp_path / "legacy.jsonl")
    _remove_observability_columns(db_path)

    source_connection = sqlite3.connect(db_path)
    source_connection.execute("PRAGMA journal_mode = WAL")
    source_connection.execute(
        "UPDATE jobs SET current_region = '서울특별시' WHERE job_id = ?", (job["job_id"],)
    )
    source_connection.commit()
    assert db_path.with_name(f"{db_path.name}-wal").exists()
    assert db_path.with_name(f"{db_path.name}-shm").exists()
    try:
        migrated = JobRepository(db_path)
    finally:
        source_connection.close()

    backup_dir = migrated.migration_backup_path
    assert backup_dir is not None
    backup_db = backup_dir / db_path.name
    with sqlite3.connect(backup_db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        restored = connection.execute(
            "SELECT current_region FROM jobs WHERE job_id = 'legacy-job'"
        ).fetchone()
    assert not set(OBSERVABILITY_COLUMNS) & columns
    assert restored == ("서울특별시",)
    assert (backup_dir / f"source-{db_path.name}-wal").is_file()
    assert (backup_dir / f"source-{db_path.name}-shm").is_file()
    manifest = json.loads((backup_dir / "backup_manifest.json").read_text(encoding="utf-8"))
    assert manifest["restore_database"] == db_path.name
    assert "source-" in manifest["sidecar_note"]
    with sqlite3.connect(db_path) as connection:
        migrated_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
    assert set(OBSERVABILITY_COLUMNS) <= migrated_columns


def test_failed_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.sqlite3"
    JobRepository(db_path)
    _remove_observability_columns(db_path)
    monkeypatch.setattr(
        "src.application.job_repository.OBSERVABILITY_COLUMNS",
        {"migration_probe": "TEXT", "invalid-column": "TEXT"},
    )

    with pytest.raises(RuntimeError, match="마이그레이션"):
        JobRepository(db_path)

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert "migration_probe" not in columns
    backups = list((tmp_path / "backups").glob("jobs_pre_migration_*"))
    assert len(backups) == 1
    assert (backups[0] / db_path.name).is_file()
