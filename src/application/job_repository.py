from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.logging_utils import sanitize
from src.models import AuditRow


ACTIVE_STATUSES = ("queued", "running", "cancel_requested")
TERMINAL_STATUSES = ("cancelled", "completed", "partial_failed", "failed")
STATUS_LABELS = {
    "queued": "대기",
    "running": "실행 중",
    "cancel_requested": "중단 요청",
    "cancelled": "중단됨",
    "completed": "완료",
    "partial_failed": "일부 실패",
    "failed": "실패",
}

JOB_FIELDS = {
    "started_at", "ended_at", "status", "current_date", "current_region",
    "current_issue", "current_issue_order", "current_issue_total", "total_regions",
    "completed_regions", "total_region_units", "known_links", "processed_links",
    "normal_count", "error_count", "excel_path", "checkpoint_path", "log_path",
    "error_message",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ActiveJobExistsError(RuntimeError):
    pass


class JobRepository:
    """프로세스 간에 공유하는 로컬 SQLite 작업 저장소."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
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
                    error_message TEXT NOT NULL DEFAULT ''
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

    def create_job(self, job_id: str, config: dict[str, Any], checkpoint_path: Path) -> dict[str, Any]:
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
                    link_delay_seconds, debug, status, total_regions, checkpoint_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id, now_iso(), config["start_date"], config["end_date"],
                    json.dumps(config["regions"], ensure_ascii=False), int(config.get("headed", False)),
                    int(config.get("resume", False)), config.get("resume_from_job_id", ""),
                    config.get("max_issues"), config.get("timeout_seconds", 30),
                    config.get("retries", 2), config.get("link_delay_seconds", 0.5),
                    int(config.get("debug", False)), len(config["regions"]), str(checkpoint_path),
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
        for name in ("headed", "resume", "debug"):
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
        for key in ("error_message", "current_issue", "current_region", "current_date"):
            if key in values:
                values[key] = sanitize(values[key])
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*values.values(), job_id),
            )

    def mark_started(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ? AND status = 'queued'",
                (now_iso(), job_id),
            )
        return cursor.rowcount == 1

    def request_cancel(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'cancel_requested' WHERE job_id = ? AND status IN ('queued', 'running')",
                (job_id,),
            )
        return cursor.rowcount == 1

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return bool(row and row["status"] == "cancel_requested")

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

    def recover_interrupted_jobs(self) -> int:
        stamp = now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id, status, processed_links FROM jobs WHERE status IN ('queued', 'running', 'cancel_requested')"
            ).fetchall()
            for row in rows:
                if row["status"] == "cancel_requested":
                    status = "cancelled"
                    message = "서버 재시작 후 이전 중단 요청을 반영했습니다."
                elif row["processed_links"]:
                    status = "partial_failed"
                    message = "서버가 재시작되어 실행을 계속할 수 없습니다. 체크포인트에서 다시 시작할 수 있습니다."
                else:
                    status = "failed"
                    message = "서버가 재시작되어 대기 또는 실행 중이던 작업이 종료되었습니다."
                connection.execute(
                    "UPDATE jobs SET status = ?, ended_at = ?, error_message = ? WHERE job_id = ?",
                    (status, stamp, message, row["job_id"]),
                )
        return len(rows)

