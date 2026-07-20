from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from src.application.job_repository import ActiveJobExistsError
from src.config import VERDICTS
from src.regions import REGION_DISPLAY_ORDER

from .schemas import JobCreateRequest

router = APIRouter(prefix="/api")


def _repository(request: Request):
    return request.app.state.job_repository


def _manager(request: Request):
    return request.app.state.job_manager


def _job_or_404(request: Request, job_id: str):
    job = _repository(request).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


@router.get("/health")
def health(request: Request):
    active = next(
        (job for job in _repository(request).list_jobs(20) if job["status"] in {"queued", "running", "cancel_requested"}),
        None,
    )
    return {"status": "ok", "local_only": True, "active_job_id": active["job_id"] if active else None}


@router.get("/config/regions")
def regions():
    return {
        "regions": [
            {"order": index, "name": name}
            for index, name in enumerate(REGION_DISPLAY_ORDER, 1)
        ]
    }


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest, request: Request):
    try:
        return _manager(request).create_job(payload.to_job_config())
    except ActiveJobExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "이미 실행 중인 작업이 있습니다.", "active_job_id": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
def list_jobs(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    return {"jobs": _repository(request).list_jobs(limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return _job_or_404(request, job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    _job_or_404(request, job_id)
    try:
        return _manager(request).cancel_job(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/results")
def get_results(
    job_id: str,
    request: Request,
    verdict: str = "",
    region: str = "",
    publisher: str = "",
    title: str = "",
    limit: int = Query(default=500, ge=1, le=1000),
):
    _job_or_404(request, job_id)
    if verdict and verdict not in VERDICTS:
        raise HTTPException(status_code=422, detail="지원하지 않는 오류 유형입니다.")
    if region and region not in REGION_DISPLAY_ORDER:
        raise HTTPException(status_code=422, detail="지원하지 않는 지역입니다.")
    total_errors, errors = _repository(request).get_errors(
        job_id, verdict=verdict, region=region, publisher=publisher, title=title, limit=limit
    )
    return {
        "summary": _repository(request).get_result_summary(job_id),
        "total_errors": total_errors,
        "errors": errors,
    }


@router.get("/jobs/{job_id}/download")
def download(job_id: str, request: Request):
    job = _job_or_404(request, job_id)
    path = Path(job["excel_path"]) if job["excel_path"] else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="다운로드할 Excel 파일이 없습니다.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/jobs/{job_id}/logs")
def logs(job_id: str, request: Request, limit: int = Query(default=100, ge=1, le=200)):
    _job_or_404(request, job_id)
    return {"logs": _repository(request).get_logs(job_id, limit)}

