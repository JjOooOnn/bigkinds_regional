from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_row
from src.checkpoint import CheckpointStore
from src.api.app import create_app
from src.application.job_manager import JobManager
from src.application.job_repository import JobRepository
from src.regions import REGION_DISPLAY_ORDER
from src.version import read_app_version


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


def test_local_runtime_exposes_headed_capability(api):
    client, _, _ = api
    assert client.get("/api/config/runtime").json() == {
        "runtime": "local", "user_headed_allowed": True,
    }
    assert client.post("/api/jobs", json=payload(headed=True)).status_code == 201


@pytest.fixture
def server_api(monkeypatch, tmp_path):
    monkeypatch.setenv("BIGKINDS_RUNTIME", "server")
    monkeypatch.setenv("ALLOWED_HOSTS", "audit.example,healthcheck.railway.app")
    monkeypatch.setenv("BIGKINDS_ACCESS_PASSWORD", "shared-test-password")
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    launched: list[str] = []
    manager = JobManager(repository, worker_launcher=launched.append, recover_on_start=False)
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<h1>audit</h1>", encoding="utf-8")
    (frontend / "assets" / "app.js").write_text("console.log('audit')", encoding="utf-8")
    app = create_app(job_manager=manager, frontend_dist=frontend)
    with TestClient(app, base_url="https://audit.example") as client:
        yield client, repository, launched


def test_server_mode_requires_hosts_and_password_before_creating_database(monkeypatch, tmp_path):
    monkeypatch.setenv("BIGKINDS_RUNTIME", "server")
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("BIGKINDS_ACCESS_PASSWORD", raising=False)
    db_path = tmp_path / "jobs.sqlite3"

    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        create_app(db_path=db_path)
    assert not db_path.exists()

    monkeypatch.setenv("ALLOWED_HOSTS", "audit.example")
    with pytest.raises(ValueError, match="BIGKINDS_ACCESS_PASSWORD"):
        create_app(db_path=db_path)
    assert not db_path.exists()


@pytest.mark.parametrize("hosts", ["*", "*.example.com", "https://audit.example", "audit.example:443"])
def test_server_mode_rejects_non_exact_hosts(monkeypatch, tmp_path, hosts):
    monkeypatch.setenv("BIGKINDS_RUNTIME", "server")
    monkeypatch.setenv("ALLOWED_HOSTS", hosts)
    monkeypatch.setenv("BIGKINDS_ACCESS_PASSWORD", "shared-test-password")
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        create_app(db_path=tmp_path / "jobs.sqlite3")


def test_server_health_is_public_and_runtime_and_files_are_protected(server_api, tmp_path):
    client, repository, launched = server_api
    auth = ("bigkinds", "shared-test-password")

    assert client.get("/api/health").json() == {
        "status": "ok", "local_only": False, "active_job_id": None,
    }
    assert client.post("/api/health").status_code == 401
    assert client.head("/api/health").status_code == 401
    for path in ("/", "/assets/app.js", "/openapi.json", "/api/config/runtime", "/api/jobs"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")
    assert client.get("/api/jobs", auth=("bigkinds", "wrong")).status_code == 401
    assert client.post("/api/jobs", json=payload()).status_code == 401
    assert client.get("/api/config/runtime", auth=auth).json() == {
        "runtime": "server", "user_headed_allowed": False,
    }
    assert client.get("/", auth=auth).status_code == 200
    assert client.get("/assets/app.js", auth=auth).status_code == 200
    assert client.get("/openapi.json", auth=auth).status_code == 200

    created = client.post("/api/jobs", json=payload(), auth=auth)
    assert created.status_code == 201
    job_id = created.json()["job_id"]
    assert launched == [job_id]
    assert client.get("/api/health").json()["active_job_id"] is None
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 401
    report = tmp_path / "report.xlsx"
    report.write_bytes(b"workbook")
    repository.update_job(job_id, excel_path=str(report), status="completed")
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 401
    assert client.get(f"/api/jobs/{job_id}/download", auth=auth).content == b"workbook"
    assert client.get("/api/health").json()["active_job_id"] is None


def test_server_mode_rejects_headed_before_spawning_worker(server_api):
    client, _, launched = server_api
    response = client.post(
        "/api/jobs", json=payload(headed=True), auth=("bigkinds", "shared-test-password"),
    )
    assert response.status_code == 422
    assert launched == []
    assert client.get("/api/jobs", auth=("bigkinds", "shared-test-password")).json()["jobs"] == []


def test_server_mode_rejects_cross_origin_changes_and_unknown_hosts(server_api):
    client, _, launched = server_api
    auth = ("bigkinds", "shared-test-password")
    cross_origin = client.post(
        "/api/jobs", json=payload(), auth=auth,
        headers={"Origin": "https://other.example"},
    )
    assert cross_origin.status_code == 403
    cross_site = client.post(
        "/api/jobs", json=payload(), auth=auth,
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site.status_code == 403
    assert launched == []
    assert client.get("/api/health", headers={"Host": "other.example"}).status_code == 400
    same_origin = client.post(
        "/api/jobs", json=payload(), auth=auth,
        headers={"Origin": "https://audit.example", "Sec-Fetch-Site": "same-origin"},
    )
    assert same_origin.status_code == 201


def test_openapi_version_matches_frontend_manifest(api):
    client, _, _ = api
    assert client.get("/openapi.json").json()["info"]["version"] == read_app_version()


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


def test_results_exclude_replaced_failure_from_summary_and_error_filter(api, tmp_path):
    client, repository, _ = api
    job = client.post("/api/jobs", json=payload()).json()
    checkpoint = CheckpointStore(tmp_path / "retry.jsonl")
    checkpoint.add_row(make_row(
        source_order=1, original_url="", final_url="", http_status=None,
        browser_result="시간 초과", link_working_yn="N", verdict="타임아웃",
        error_message="deadline",
    ))
    checkpoint.add_row(make_row(
        source_order=1, original_url="https://example.com/retry",
        final_url="https://example.com/retry", verdict="정상", link_working_yn="Y",
        browser_result="정상 표시", error_message="",
    ))
    repository.replace_results(job["job_id"], checkpoint.rows)

    response = client.get(
        f"/api/jobs/{job['job_id']}/results", params={"verdict": "타임아웃"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_links"] == 1
    assert body["summary"]["normal_count"] == 1
    assert body["summary"]["error_count"] == 0
    assert body["total_errors"] == 0
    assert body["errors"] == []


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
