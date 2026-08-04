from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from src.checkpoint import CheckpointStore
from src.logging_utils import sanitize
from src.models import AuditRow


CANCELLATION_STATUSES = ("cancel_requested", "cancelling", "force_terminating")
ACTIVE_STATUSES = ("queued", "running", *CANCELLATION_STATUSES)
TERMINAL_STATUSES = ("cancelled", "completed", "partial_failed", "failed")
STATUS_LABELS = {
    "queued": "대기",
    "running": "실행 중",
    "cancel_requested": "중단 요청",
    "cancelling": "중단 처리 중",
    "force_terminating": "강제 종료 중",
    "cancelled": "중단됨",
    "completed": "완료",
    "partial_failed": "일부 실패",
    "failed": "실패",
}

JOB_FIELDS = {
    "started_at", "ended_at", "status", "current_date", "current_region",
    "current_issue", "current_issue_order", "current_issue_total", "total_regions",
    "current_region_completed_issues", "current_region_total_issues",
    "current_issue_processed_articles", "current_issue_total_articles",
    "current_publisher", "current_article_title",
    "completed_regions", "total_region_units", "known_links", "processed_links",
    "normal_count", "error_count", "excel_path", "checkpoint_path", "log_path",
    "error_message", "worker_pid", "worker_exit_code", "attempt_number",
    "heartbeat_at", "last_progress_at", "current_operation",
    "operation_started_at", "browser_state", "browser_restart_count",
    "cancel_requested_at", "cancel_requested_by", "termination_reason",
    "manual_resume_available", "manual_resume_reason", "checkpoint_state",
}

OBSERVABILITY_COLUMNS = {
    "current_region_completed_issues": "INTEGER NOT NULL DEFAULT 0",
    "current_region_total_issues": "INTEGER",
    "current_issue_processed_articles": "INTEGER NOT NULL DEFAULT 0",
    "current_issue_total_articles": "INTEGER",
    "current_publisher": "TEXT NOT NULL DEFAULT ''",
    "current_article_title": "TEXT NOT NULL DEFAULT ''",
    "worker_pid": "INTEGER",
    "worker_exit_code": "INTEGER",
    "attempt_number": "INTEGER NOT NULL DEFAULT 0",
    "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
    "last_progress_at": "TEXT NOT NULL DEFAULT ''",
    "current_operation": "TEXT NOT NULL DEFAULT ''",
    "operation_started_at": "TEXT NOT NULL DEFAULT ''",
    "browser_state": "TEXT NOT NULL DEFAULT 'not_started'",
    "browser_restart_count": "INTEGER NOT NULL DEFAULT 0",
    "cancel_requested_at": "TEXT NOT NULL DEFAULT ''",
    "cancel_requested_by": "TEXT NOT NULL DEFAULT ''",
    "termination_reason": "TEXT NOT NULL DEFAULT ''",
    "manual_resume_available": "INTEGER NOT NULL DEFAULT 0",
    "manual_resume_reason": "TEXT NOT NULL DEFAULT ''",
    "checkpoint_state": "TEXT NOT NULL DEFAULT ''",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ActiveJobExistsError(RuntimeError):
    pass


class JobRepository:
    """프로세스 간에 공유하는 로컬 SQLite 작업 저장소."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.migration_backup_path: Path | None = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            jobs_table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone() is not None
            existing_columns = (
                {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if jobs_table_exists else set()
            )
            missing_columns = [
                name for name in OBSERVABILITY_COLUMNS if name not in existing_columns
            ]
            if jobs_table_exists and missing_columns:
                try:
                    self.migration_backup_path = self._backup_before_migration(connection)
                except Exception as exc:
                    raise RuntimeError(
                        f"SQLite 마이그레이션 전 백업에 실패했습니다: {sanitize(exc)}"
                    ) from exc

            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    ended_at TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    regions_json TEXT NOT NULL,
                    headed INTEGER NOT NULL DEFAULT 0,
                    resume INTEGER NOT NULL DEFAULT 0,
                    resume_from_job_id TEXT NOT NULL DEFAULT '',
                    max_issues INTEGER,
                    timeout_seconds INTEGER NOT NULL DEFAULT 30,
                    retries INTEGER NOT NULL DEFAULT 2,
                    link_delay_seconds REAL NOT NULL DEFAULT 0.5,
                    debug INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    current_date TEXT NOT NULL DEFAULT '',
                    current_region TEXT NOT NULL DEFAULT '',
                    current_issue TEXT NOT NULL DEFAULT '',
                    current_issue_order INTEGER NOT NULL DEFAULT 0,
                    current_issue_total INTEGER NOT NULL DEFAULT 0,
                    current_region_completed_issues INTEGER NOT NULL DEFAULT 0,
                    current_region_total_issues INTEGER,
                    current_issue_processed_articles INTEGER NOT NULL DEFAULT 0,
                    current_issue_total_articles INTEGER,
                    current_publisher TEXT NOT NULL DEFAULT '',
                    current_article_title TEXT NOT NULL DEFAULT '',
                    total_regions INTEGER NOT NULL DEFAULT 0,
                    completed_regions INTEGER NOT NULL DEFAULT 0,
                    total_region_units INTEGER NOT NULL DEFAULT 0,
                    known_links INTEGER NOT NULL DEFAULT 0,
                    processed_links INTEGER NOT NULL DEFAULT 0,
                    normal_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    excel_path TEXT NOT NULL DEFAULT '',
                    checkpoint_path TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    worker_pid INTEGER,
                    worker_exit_code INTEGER,
                    attempt_number INTEGER NOT NULL DEFAULT 0,
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    last_progress_at TEXT NOT NULL DEFAULT '',
                    current_operation TEXT NOT NULL DEFAULT '',
                    operation_started_at TEXT NOT NULL DEFAULT '',
                    browser_state TEXT NOT NULL DEFAULT 'not_started',
                    browser_restart_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT NOT NULL DEFAULT '',
                    cancel_requested_by TEXT NOT NULL DEFAULT '',
                    termination_reason TEXT NOT NULL DEFAULT '',
                    manual_resume_available INTEGER NOT NULL DEFAULT 0,
                    manual_resume_reason TEXT NOT NULL DEFAULT '',
                    checkpoint_state TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id, id DESC);

                CREATE TABLE IF NOT EXISTS job_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    requested_date TEXT NOT NULL,
                    region TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    article_title TEXT NOT NULL,
                    link_working_yn TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    original_url TEXT NOT NULL,
                    final_url TEXT NOT NULL,
                    http_status INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_job_results_job_id ON job_results(job_id);
                CREATE INDEX IF NOT EXISTS idx_job_results_filters
                    ON job_results(job_id, link_working_yn, verdict, region);
                """
            )
            if jobs_table_exists and missing_columns:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    for name in missing_columns:
                        connection.execute(
                            f"ALTER TABLE jobs ADD COLUMN {name} {OBSERVABILITY_COLUMNS[name]}"
                        )
                    connection.commit()
                except Exception as exc:
                    connection.rollback()
                    raise RuntimeError(
                        "SQLite 마이그레이션에 실패했습니다. "
                        f"백업: {self.migration_backup_path}. 원인: {sanitize(exc)}"
                    ) from exc

    def _backup_before_migration(self, connection: sqlite3.Connection) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.db_path.parent / "backups" / (
            f"{self.db_path.stem}_pre_migration_{stamp}"
        )
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup_db = backup_dir / self.db_path.name
        with sqlite3.connect(backup_db) as backup_connection:
            connection.backup(backup_connection)

        copied_sidecars = []
        for suffix in ("-wal", "-shm"):
            source = self.db_path.with_name(f"{self.db_path.name}{suffix}")
            if source.is_file():
                destination = backup_dir / f"source-{self.db_path.name}{suffix}"
                shutil.copy2(source, destination)
                copied_sidecars.append(destination.name)

        manifest = {
            "created_at": now_iso(),
            "source_database": str(self.db_path),
            "restore_database": backup_db.name,
            "source_sidecars": copied_sidecars,
            "sidecar_note": (
                "source- 접두사 파일은 진단 보존용입니다. 복구 시 서버를 중지하고 "
                "restore_database만 원래 위치에 복원한 뒤 WAL/SHM은 SQLite가 다시 생성하게 하세요."
            ),
        }
        (backup_dir / "backup_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return backup_dir

    def create_job(self, job_id: str, config: dict[str, Any], checkpoint_path: Path) -> dict[str, Any]:
        total_region_units = (
            (date.fromisoformat(config["end_date"]) - date.fromisoformat(config["start_date"])).days + 1
        ) * len(config["regions"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                f"SELECT job_id FROM jobs WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)}) LIMIT 1",
                ACTIVE_STATUSES,
            ).fetchone()
            if active:
                raise ActiveJobExistsError(str(active["job_id"]))
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, created_at, start_date, end_date, regions_json, headed, resume,
                    resume_from_job_id, max_issues, timeout_seconds, retries,
                    link_delay_seconds, debug, status, total_regions, total_region_units, checkpoint_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id, now_iso(), config["start_date"], config["end_date"],
                    json.dumps(config["regions"], ensure_ascii=False), int(config.get("headed", False)),
                    int(config.get("resume", False)), config.get("resume_from_job_id", ""),
                    config.get("max_issues"), config.get("timeout_seconds", 30),
                    config.get("retries", 2), config.get("link_delay_seconds", 0.5),
                    int(config.get("debug", False)), len(config["regions"]), total_region_units,
                    str(checkpoint_path),
                ),
            )
        return self.get_job(job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._decode_job(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    @staticmethod
    def _decode_job(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["regions"] = json.loads(item.pop("regions_json"))
        for name in ("headed", "resume", "debug", "manual_resume_available"):
            item[name] = bool(item[name])
        item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
        excel = Path(item["excel_path"]) if item["excel_path"] else None
        item["download_available"] = bool(excel and excel.is_file())
        item["excel_file_name"] = excel.name if excel else ""
        total_units = item["total_region_units"]
        item["progress_percent"] = (
            min(100.0, item["completed_regions"] / total_units * 100) if total_units else 0.0
        )
        return item

    def update_job(self, job_id: str, **fields: Any) -> None:
        values = {key: value for key, value in fields.items() if key in JOB_FIELDS}
        if not values:
            return
        for key in (
            "error_message", "current_issue", "current_region", "current_date",
            "current_publisher", "current_article_title", "current_operation",
            "browser_state", "cancel_requested_by",
            "termination_reason", "manual_resume_reason", "checkpoint_state",
        ):
            if key in values:
                values[key] = sanitize(values[key])
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*values.values(), job_id),
            )

    def mark_started(self, job_id: str, worker_pid: int) -> bool:
        stamp = now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?, worker_pid = ?,
                    attempt_number = attempt_number + 1, heartbeat_at = ?,
                    current_operation = 'worker_starting', operation_started_at = ?,
                    termination_reason = ''
                WHERE job_id = ? AND status = 'queued'
                """,
                (stamp, worker_pid, stamp, stamp, job_id),
            )
        return cursor.rowcount == 1

    def record_worker_spawned(self, job_id: str, worker_pid: int) -> None:
        self.update_job(job_id, worker_pid=worker_pid)

    def touch_heartbeat(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE jobs SET heartbeat_at = ?
                WHERE job_id = ? AND status IN ({','.join('?' for _ in ('running', *CANCELLATION_STATUSES))})
                """,
                (now_iso(), job_id, "running", *CANCELLATION_STATUSES),
            )
        return cursor.rowcount == 1

    def request_cancel(self, job_id: str, requested_by: str = "api") -> bool:
        stamp = now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'cancel_requested', cancel_requested_at = ?, cancel_requested_by = ?
                WHERE job_id = ? AND status IN ('queued', 'running')
                """,
                (stamp, sanitize(requested_by), job_id),
            )
            if cursor.rowcount == 1:
                return True
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["status"] in CANCELLATION_STATUSES)

    def acknowledge_cancel(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'cancelling' WHERE job_id = ? AND status = 'cancel_requested'",
                (job_id,),
            )
            if cursor.rowcount == 1:
                return True
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["status"] in {"cancelling", "force_terminating"})

    def mark_force_terminating(self, job_id: str, worker_pid: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'force_terminating'
                WHERE job_id = ? AND worker_pid = ?
                  AND status IN ('cancel_requested', 'cancelling')
                """,
                (job_id, worker_pid),
            )
        return cursor.rowcount == 1

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return bool(row and row["status"] in CANCELLATION_STATUSES)

    def append_log(self, job_id: str, message: str, level: str = "info") -> None:
        clean_message = sanitize(message)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO job_logs(job_id, created_at, level, message) VALUES (?, ?, ?, ?)",
                (job_id, now_iso(), level, clean_message),
            )
            connection.execute(
                """
                DELETE FROM job_logs WHERE job_id = ? AND id NOT IN (
                    SELECT id FROM job_logs WHERE job_id = ? ORDER BY id DESC LIMIT 200
                )
                """,
                (job_id, job_id),
            )

    def get_logs(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, level, message FROM job_logs
                WHERE job_id = ? ORDER BY id DESC LIMIT ?
                """,
                (job_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def replace_results(self, job_id: str, rows: Iterable[AuditRow]) -> None:
        values = []
        for row in rows:
            values.append((
                job_id,
                row.requested_date,
                row.region,
                sanitize(row.publisher),
                sanitize(row.article_title),
                row.link_working_yn,
                row.verdict,
                sanitize(row.error_message),
                sanitize(row.original_url),
                sanitize(row.final_url),
                row.http_status,
            ))
        with self._connect() as connection:
            connection.execute("DELETE FROM job_results WHERE job_id = ?", (job_id,))
            connection.executemany(
                """
                INSERT INTO job_results (
                    job_id, requested_date, region, publisher, article_title,
                    link_working_yn, verdict, error_message, original_url, final_url, http_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def get_result_summary(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN verdict = '정상' THEN 1 ELSE 0 END) AS normal,
                       SUM(CASE WHEN verdict <> '정상' THEN 1 ELSE 0 END) AS errors
                FROM job_results WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            verdict_rows = connection.execute(
                "SELECT verdict, COUNT(*) AS count FROM job_results WHERE job_id = ? GROUP BY verdict",
                (job_id,),
            ).fetchall()
        total = int(counts["total"] or 0)
        normal = int(counts["normal"] or 0)
        errors = int(counts["errors"] or 0)
        return {
            "total_links": total,
            "normal_count": normal,
            "error_count": errors,
            "normal_rate": normal / total if total else 0,
            "verdict_counts": {row["verdict"]: row["count"] for row in verdict_rows},
        }

    def get_errors(
        self,
        job_id: str,
        *,
        verdict: str = "",
        region: str = "",
        publisher: str = "",
        title: str = "",
        limit: int = 500,
    ) -> tuple[int, list[dict[str, Any]]]:
        where = ["job_id = ?", "link_working_yn = 'N'"]
        params: list[Any] = [job_id]
        for column, value in (("verdict", verdict), ("region", region)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        for column, value in (("publisher", publisher), ("article_title", title)):
            if value:
                where.append(f"{column} LIKE ?")
                params.append(f"%{value}%")
        clause = " AND ".join(where)
        with self._connect() as connection:
            count = connection.execute(
                f"SELECT COUNT(*) AS count FROM job_results WHERE {clause}", params
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT requested_date, region, publisher, article_title, link_working_yn,
                       verdict, error_message, original_url, final_url, http_status
                FROM job_results WHERE {clause}
                ORDER BY requested_date, region, id LIMIT ?
                """,
                (*params, max(1, min(limit, 1000))),
            ).fetchall()
        return int(count), [dict(row) for row in rows]

    @staticmethod
    def _checkpoint_resume_details(
        job: dict[str, Any], status: str,
    ) -> tuple[dict[str, Any], list[AuditRow], bool]:
        checkpoint_path = Path(job.get("checkpoint_path") or "")
        if not checkpoint_path.is_file():
            return {
                "manual_resume_available": False,
                "manual_resume_reason": (
                    "취소된 작업은 재개할 수 없습니다."
                    if status == "cancelled" else "재개할 체크포인트 파일이 없습니다."
                ),
                "checkpoint_state": "missing",
            }, [], False
        try:
            checkpoint = CheckpointStore(checkpoint_path, resume=True)
            config = checkpoint.stored_run_config
            if not config:
                raise ValueError("실행 조건이 없습니다.")
            start = date.fromisoformat(str(config["start_date"]))
            end = date.fromisoformat(str(config["end_date"]))
            regions = list(config.get("regions") or [])
            total_units = ((end - start).days + 1) * len(regions)
            checkpoint_state = (
                "complete" if total_units and len(checkpoint.completed) >= total_units
                else "incomplete"
            )
        except Exception as exc:
            return {
                "manual_resume_available": False,
                "manual_resume_reason": (
                    "취소된 작업은 재개할 수 없습니다."
                    if status == "cancelled"
                    else f"체크포인트를 읽을 수 없습니다: {sanitize(exc)}"
                ),
                "checkpoint_state": "invalid",
            }, [], False

        if status == "cancelled":
            available = False
            reason = "취소된 작업은 재개할 수 없습니다."
        elif status not in {"partial_failed", "failed"}:
            available = False
            reason = "종료 상태가 수동 재개 대상이 아닙니다."
        elif checkpoint_state == "complete":
            available = False
            reason = "체크포인트에 완료된 작업으로 기록되어 있습니다."
        else:
            available = True
            reason = "체크포인트에서 수동으로 재개할 수 있습니다."
        return {
            "manual_resume_available": available,
            "manual_resume_reason": reason,
            "checkpoint_state": checkpoint_state,
        }, list(checkpoint.rows), True

    def refresh_resume_metadata(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        metadata, _, _ = self._checkpoint_resume_details(job, job["status"])
        self.update_job(job_id, **metadata)
        return self.get_job(job_id)  # type: ignore[return-value]

    def finalize_interrupted_job(
        self,
        job_id: str,
        *,
        status: str,
        error_message: str,
        termination_reason: str,
        worker_exit_code: int | None = None,
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        metadata, rows, checkpoint_loaded = self._checkpoint_resume_details(job, status)
        if status == "failed" and rows:
            status = "partial_failed"
            metadata, rows, checkpoint_loaded = self._checkpoint_resume_details(job, status)
        fields: dict[str, Any] = {
            "status": status,
            "ended_at": now_iso(),
            "error_message": error_message,
            "termination_reason": termination_reason,
            **metadata,
        }
        if worker_exit_code is not None:
            fields["worker_exit_code"] = worker_exit_code
        if checkpoint_loaded:
            self.replace_results(job_id, rows)
            normal_count = sum(row.verdict == "정상" for row in rows)
            fields.update(
                processed_links=len(rows),
                normal_count=normal_count,
                error_count=len(rows) - normal_count,
            )
        self.update_job(job_id, **fields)
        return self.get_job(job_id)  # type: ignore[return-value]

    def recover_interrupted_jobs(self) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT job_id FROM jobs
                WHERE status IN ({','.join('?' for _ in ACTIVE_STATUSES)})
                """,
                ACTIVE_STATUSES,
            ).fetchall()
        for row in rows:
            job = self.get_job(row["job_id"])
            if not job:
                continue
            if job["status"] in CANCELLATION_STATUSES:
                status = "cancelled"
                message = "서버 재시작 후 이전 중단 요청을 반영했습니다."
                reason = "server_restart_after_cancel"
            elif job["processed_links"]:
                status = "partial_failed"
                message = "서버가 재시작되어 실행을 계속할 수 없습니다."
                reason = "server_restart"
            else:
                _, checkpoint_rows, _ = self._checkpoint_resume_details(
                    job, "partial_failed",
                )
                status = "partial_failed" if checkpoint_rows else "failed"
                message = "서버가 재시작되어 대기 또는 실행 중이던 작업이 종료되었습니다."
                reason = "server_restart"
            self.finalize_interrupted_job(
                job["job_id"], status=status, error_message=message,
                termination_reason=reason,
            )
        return len(rows)
