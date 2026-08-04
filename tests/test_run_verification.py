from __future__ import annotations

from datetime import datetime

from conftest import make_row
from src.application.job_repository import JobRepository
from src.application.run_verification import compare_job_artifact_counts
from src.checkpoint import CheckpointStore
from src.excel_writer import write_excel


def _config():
    return {
        "start_date": "2026-07-08",
        "end_date": "2026-07-08",
        "regions": ["충청북도"],
    }


def test_compares_job_checkpoint_sqlite_and_excel_rows(tmp_path):
    repository = JobRepository(tmp_path / "web_jobs.sqlite3")
    checkpoint_path = tmp_path / "checkpoint.jsonl"
    job = repository.create_job("job-1", _config(), checkpoint_path)
    checkpoint = CheckpointStore(checkpoint_path, run_config={**_config(), "selection_mode": "선택"})
    rows = [
        make_row(article_title="첫 번째 기사"),
        make_row(
            article_title="두 번째 기사", source_order=2,
            original_url="https://example.com/b", final_url="https://example.com/b",
        ),
    ]
    for row in rows:
        checkpoint.add_row(row)
    excel_path = write_excel(
        tmp_path / "report.xlsx", rows, [],
        start_date="2026-07-08", end_date="2026-07-08",
        started_at=datetime.now().astimezone(), ended_at=datetime.now().astimezone(),
        selected_regions=["충청북도"],
    )
    repository.replace_results(job["job_id"], rows)
    repository.update_job(
        job["job_id"], status="completed", processed_links=len(rows),
        excel_path=str(excel_path), checkpoint_path=str(checkpoint_path),
    )

    comparison = compare_job_artifact_counts(repository, job["job_id"])

    assert comparison.to_dict() == {
        "job_id": "job-1",
        "processed_links": 2,
        "checkpoint_rows": 2,
        "sqlite_rows": 2,
        "excel_rows": 2,
        "matches": True,
    }


def test_reports_mismatched_processed_link_count(tmp_path):
    repository = JobRepository(tmp_path / "web_jobs.sqlite3")
    checkpoint_path = tmp_path / "checkpoint.jsonl"
    job = repository.create_job("job-1", _config(), checkpoint_path)
    checkpoint = CheckpointStore(checkpoint_path, run_config={**_config(), "selection_mode": "선택"})
    row = make_row()
    checkpoint.add_row(row)
    excel_path = write_excel(
        tmp_path / "report.xlsx", [row], [],
        start_date="2026-07-08", end_date="2026-07-08",
        started_at=datetime.now().astimezone(), ended_at=datetime.now().astimezone(),
        selected_regions=["충청북도"],
    )
    repository.replace_results(job["job_id"], [row])
    repository.update_job(
        job["job_id"], status="partial_failed", processed_links=2,
        excel_path=str(excel_path), checkpoint_path=str(checkpoint_path),
    )

    comparison = compare_job_artifact_counts(repository, job["job_id"])

    assert comparison.matches is False
    assert comparison.checkpoint_rows == comparison.sqlite_rows == comparison.excel_rows == 1
