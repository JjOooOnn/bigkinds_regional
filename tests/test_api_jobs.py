from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_row
from src.api.app import create_app
from src.application.job_manager import JobManager
from src.application.job_repository import JobRepository
from src.regions import REGION_DISPLAY_ORDER


@pytest.fixture
def api(tmp_path):
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    launched: list[str] = []
    manager = JobManager(
        repository,
        worker_launcher=launched.append,
        recover_on_start=False,
    )
    app = create_app(
        job_manager=manager,
        frontend_dist=tmp_path / "missing-frontend",
    )
    with TestClient(app) as client:
        yield client, repository, launched


def payload(**overrides):
    data = {
        "start_date": "2026-07-08",
        "end_date": "2026-07-08",
        "all_regions": False,
        "regions": ["충청북도"],
        "headed": False,
    }
    data.update(overrides)
    return data


def test_health_and_region_order(api):
    client, _, _ = api
    assert client.get("/api/health").json() == {
        "status": "ok", "local_only": True, "active_job_id": None,
    }
    response = client.get("/api/config/regions")
    assert response.status_code == 200
    assert [item["name"] for item in response.json()["regions"]] == list(REGION_DISPLAY_ORDER)


def test_create_job_with_all_regions_and_read_status(api):
    client, _, launched = api
    response = client.post("/api/jobs", json=payload(all_regions=True, regions=[]))
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "queued"
    assert job["attempt_number"] == 0
    assert job["browser_state"] == "not_started"
    assert job["manual_resume_available"] is False
    assert job["heartbeat_at"] == ""
    assert job["regions"] == list(REGION_DISPLAY_ORDER)
    assert job["total_regions"] == 17
    assert job["total_region_units"] == 17
    assert launched == [job["job_id"]]
    detail = client.get(f"/api/jobs/{job['job_id']}")
    assert detail.status_code == 200
    assert detail.json()["start_date"] == "2026-07-08"


def test_create_job_with_multiple_selected_regions(api):
    client, _, _ = api
    selected = ["충청북도", "충청남도"]
    response = client.post("/api/jobs", json=payload(regions=selected))
    assert response.status_code == 201
    assert response.json()["regions"] == selected


def test_job_status_returns_current_region_issue_and_article_progress(api):
    client, repository, _ = api
    job = client.post("/api/jobs", json=payload()).json()
    repository.update_job(
        job["job_id"],
        current_date="2026-07-08",
        current_region="충청북도",
        current_region_completed_issues=2,
        current_region_total_issues=5,
        current_issue="집중호우 대응",
        current_issue_order=3,
        current_issue_total=5,
        current_issue_processed_articles=4,
        current_issue_total_articles=7,
        current_publisher="테스트일보",
        current_article_title="집중호우 대응 기사",
    )

    observed = client.get(f"/api/jobs/{job['job_id']}").json()
    assert observed["current_region_completed_issues"] == 2
    assert observed["current_region_total_issues"] == 5
    assert observed["current_issue_processed_articles"] == 4
    assert observed["current_issue_total_articles"] == 7
    assert observed["current_publisher"] == "테스트일보"
    assert observed["current_article_title"] == "집중호우 대응 기사"


@pytest.mark.parametrize(
    "invalid_payload",
    [
        payload(start_date="2026-07-09", end_date="2026-07-08"),
        payload(regions=[]),
        payload(regions=["충청도"]),
    ],
)
def test_invalid_dates_and_regions_are_rejected(api, invalid_payload):
    client, _, _ = api
    response = client.post("/api/jobs", json=invalid_payload)
    assert response.status_code == 422


def test_duplicate_active_job_is_rejected(api):
    client, _, _ = api
    first = client.post("/api/jobs", json=payload())
    second = client.post("/api/jobs", json=payload(regions=["충청남도"]))
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["active_job_id"] == first.json()["job_id"]


def test_cancel_request_is_idempotent_through_cancelled(api):
    client, repository, _ = api
    job = client.post("/api/jobs", json=payload()).json()
    cancelled = client.post(f"/api/jobs/{job['job_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancel_requested"
    assert cancelled.json()["cancel_requested_at"]
    assert cancelled.json()["cancel_requested_by"] == "user"
    for status in ("cancel_requested", "cancelling", "force_terminating"):
        repository.update_job(job["job_id"], status=status)
        repeated = client.post(f"/api/jobs/{job['job_id']}/cancel")
        assert repeated.status_code == 200
        assert repeated.json()["status"] == status
    repository.update_job(job["job_id"], status="cancelled")
    repeated = client.post(f"/api/jobs/{job['job_id']}/cancel")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"


def test_resume_uses_previous_checkpoint_with_same_scope(api):
    client, repository, launched = api
    previous = client.post("/api/jobs", json=payload()).json()
    checkpoint = repository.get_job(previous["job_id"])["checkpoint_path"]
    Path(checkpoint).parent.mkdir(parents=True, exist_ok=True)
    Path(checkpoint).write_text(
        '{"type":"run_config","data":{"start_date":"2026-07-08","end_date":"2026-07-08","regions":["충청북도"],"selection_mode":"선택"}}\n',
        encoding="utf-8",
    )
    repository.update_job(previous["job_id"], status="partial_failed")
    resumed = client.post(
        "/api/jobs",
        json=payload(resume=True, resume_from_job_id=previous["job_id"]),
    )
    assert resumed.status_code == 201
    assert resumed.json()["resume"] is True
    assert resumed.json()["checkpoint_path"] == checkpoint
    assert launched[-1] == resumed.json()["job_id"]


def test_cancelled_job_cannot_be_resumed_even_with_a_checkpoint(api):
    client, repository, _ = api
    previous = client.post("/api/jobs", json=payload()).json()
    checkpoint = Path(repository.get_job(previous["job_id"])["checkpoint_path"])
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(
        '{"type":"run_config","data":{"start_date":"2026-07-08","end_date":"2026-07-08","regions":["충청북도"],"selection_mode":"선택"}}\n',
        encoding="utf-8",
    )
    repository.update_job(previous["job_id"], status="cancelled")

    response = client.post(
        "/api/jobs",
        json=payload(resume=True, resume_from_job_id=previous["job_id"]),
    )
    assert response.status_code == 422
    assert "취소된 작업" in response.json()["detail"]


def test_results_filters_and_logs(api):
    client, repository, _ = api
    job = client.post("/api/jobs", json=payload()).json()
    job_id = job["job_id"]
    repository.replace_results(
        job_id,
        [
            make_row(),
            make_row(
                original_url="https://example.com/missing",
                final_url="https://example.com/missing",
                region="충청북도",
                publisher="오류일보",
                article_title="찾을 수 없는 기사",
                verdict="링크오류",
                link_working_yn="N",
                error_message="기사 페이지를 찾을 수 없음",
            ),
        ],
    )
    repository.append_log(job_id, "충청북도 점검 완료")
    response = client.get(
        f"/api/jobs/{job_id}/results",
        params={"verdict": "링크오류", "publisher": "오류"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_links"] == 2
    assert body["summary"]["error_count"] == 1
    assert body["total_errors"] == 1
    assert body["errors"][0]["original_url"] == "https://example.com/missing"
    logs = client.get(f"/api/jobs/{job_id}/logs").json()["logs"]
    assert logs[-1]["message"] == "충청북도 점검 완료"


def test_excel_download_and_missing_file(api, tmp_path):
    client, repository, _ = api
    job = client.post("/api/jobs", json=payload()).json()
    missing = client.get(f"/api/jobs/{job['job_id']}/download")
    assert missing.status_code == 404
    excel = tmp_path / "report.xlsx"
    excel.write_bytes(b"test workbook")
    repository.update_job(job["job_id"], excel_path=str(excel), status="completed")
    response = client.get(f"/api/jobs/{job['job_id']}/download")
    assert response.status_code == 200
    assert response.content == b"test workbook"
    assert "report.xlsx" in response.headers["content-disposition"]


def test_unknown_job_endpoints_return_404(api):
    client, _, _ = api
    for method, path in (
        ("get", "/api/jobs/not-found"),
        ("post", "/api/jobs/not-found/cancel"),
        ("get", "/api/jobs/not-found/results"),
        ("get", "/api/jobs/not-found/download"),
        ("get", "/api/jobs/not-found/logs"),
    ):
        assert getattr(client, method)(path).status_code == 404
