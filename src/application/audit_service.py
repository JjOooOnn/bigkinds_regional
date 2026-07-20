from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.checkpoint import CheckpointStore
from src.config import OUTPUT_DIR, WORK_DIR
from src.date_navigation import validate_date_range
from src.excel_writer import write_excel
from src.logging_utils import sanitize, setup_logging
from src.models import AuditRow, DebugEntry
from src.regional_collector import RegionalCollector
from src.regions import REGION_DISPLAY_ORDER, SUPPORTED_REGIONS, is_all_regions, resume_checkpoint_path

from .progress import (
    AuditCancelled,
    CancellationToken,
    NeverCancelToken,
    NullProgressReporter,
    ProgressReporter,
)


@dataclass(frozen=True)
class AuditRequest:
    start_date: str
    end_date: str
    regions: list[str]
    headed: bool = False
    resume: bool = False
    max_issues: int | None = None
    timeout_seconds: int = 30
    retries: int = 2
    link_delay_seconds: float = 0.5
    debug: bool = False
    checkpoint_path: Path | None = None
    output_tag: str = ""

    def validated_dates(self):
        if not self.regions:
            raise ValueError("점검할 지역을 하나 이상 선택해 주세요.")
        unknown = [region for region in self.regions if region not in SUPPORTED_REGIONS]
        if unknown:
            raise ValueError(f"존재하지 않는 지역명입니다: {', '.join(unknown)}")
        if len(set(self.regions)) != len(self.regions):
            raise ValueError("선택 지역에 중복된 값이 있습니다.")
        if self.max_issues is not None and self.max_issues < 1:
            raise ValueError("max_issues는 1 이상이어야 합니다.")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds는 1 이상이어야 합니다.")
        if self.retries not in range(0, 3):
            raise ValueError("retries는 0부터 2까지 지정할 수 있습니다.")
        if self.link_delay_seconds < 0:
            raise ValueError("link_delay_seconds는 0 이상이어야 합니다.")
        return validate_date_range(self.start_date, self.end_date)


@dataclass
class AuditRunResult:
    status: str
    started_at: datetime
    ended_at: datetime
    excel_path: Path | None
    checkpoint_path: Path
    log_path: Path
    rows: list[AuditRow] = field(default_factory=list)
    debug_entries: list[DebugEntry] = field(default_factory=list)
    completed_region_units: int = 0
    issue_count: int = 0
    error_message: str = ""


class AuditService:
    """기존 collector/checkpoint/Excel 구현을 조립하는 공통 실행 서비스."""

    def __init__(self, *, output_dir: Path = OUTPUT_DIR, work_dir: Path = WORK_DIR):
        self.output_dir = Path(output_dir)
        self.work_dir = Path(work_dir)

    def run(
        self,
        request: AuditRequest,
        *,
        reporter: ProgressReporter | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AuditRunResult:
        start_date, end_date = request.validated_dates()
        reporter = reporter or NullProgressReporter()
        cancellation_token = cancellation_token or NeverCancelToken()
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        started_at = datetime.now().astimezone()
        suffix = f"_{request.output_tag}" if request.output_tag else ""
        log_path = self.work_dir / f"audit_{request.start_date}_{request.end_date}_{run_stamp}{suffix}.log"
        logger = setup_logging(log_path)

        selected_checkpoint = request.checkpoint_path or resume_checkpoint_path(
            self.work_dir,
            request.start_date,
            request.end_date,
            request.regions,
            resume=request.resume,
        )
        checkpoint_config = {
            "start_date": request.start_date,
            "end_date": request.end_date,
            "regions": request.regions,
            "selection_mode": "전체" if is_all_regions(request.regions) else "선택",
        }
        checkpoint = CheckpointStore(
            selected_checkpoint,
            resume=request.resume,
            run_config=checkpoint_config,
        )
        collector = RegionalCollector(
            start_date=start_date,
            end_date=end_date,
            regions=list(request.regions),
            headed=request.headed,
            max_issues=request.max_issues,
            timeout_ms=request.timeout_seconds * 1000,
            retries=request.retries,
            link_delay_ms=max(0, int(request.link_delay_seconds * 1000)),
            checkpoint=checkpoint,
            logger=logger,
            debug=request.debug,
            progress_reporter=reporter,
            cancellation_token=cancellation_token,
        )

        status = "completed"
        error_message = ""
        try:
            if cancellation_token.is_cancel_requested():
                raise AuditCancelled("사용자가 점검 중단을 요청했습니다.")
            asyncio.run(collector.run())
            if collector.had_partial_failures:
                status = "partial_failed"
        except (AuditCancelled, KeyboardInterrupt):
            status = "cancelled"
            logger.warning("사용자 중단: 현재까지 수집한 결과를 저장합니다.")
        except Exception as exc:
            status = "partial_failed" if checkpoint.rows else "failed"
            error_message = sanitize(exc)
            logger.error(
                "실행 중 오류가 발생했지만 현재까지 결과를 저장합니다: %s",
                error_message,
            )

        ended_at = datetime.now().astimezone()
        output_path = self.output_dir / (
            f"bigkinds_regional_link_audit_{request.start_date}_{request.end_date}_"
            f"{run_stamp}{suffix}.xlsx"
        )
        excel_path: Path | None = None
        try:
            completed_region_units = len(checkpoint.completed)
            excel_path = write_excel(
                output_path,
                checkpoint.rows,
                checkpoint.debug_entries,
                start_date=request.start_date,
                end_date=request.end_date,
                started_at=started_at,
                ended_at=ended_at,
                region_count=completed_region_units,
                issue_count=len(checkpoint.issue_keys),
                selected_regions=request.regions,
            )
        except Exception as exc:
            status = "failed"
            error_message = sanitize(exc)
            logger.error("Excel 저장 실패: %s", error_message)

        reporter.emit(
            "audit_finished",
            "점검이 중단되었습니다." if status == "cancelled" else "점검이 완료되었습니다.",
            status=status,
            processed_links=len(checkpoint.rows),
            normal_count=sum(row.verdict == "정상" for row in checkpoint.rows),
            error_count=sum(row.verdict != "정상" for row in checkpoint.rows),
            excel_path=str(excel_path or ""),
            error_message=error_message,
        )
        return AuditRunResult(
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            excel_path=excel_path,
            checkpoint_path=selected_checkpoint,
            log_path=log_path,
            rows=list(checkpoint.rows),
            debug_entries=list(checkpoint.debug_entries),
            completed_region_units=len(checkpoint.completed),
            issue_count=len(checkpoint.issue_keys),
            error_message=error_message,
        )


def all_regions() -> list[str]:
    return list(REGION_DISPLAY_ORDER)
