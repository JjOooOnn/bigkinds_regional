from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from src.checkpoint import CheckpointStore
from src.config import OUTPUT_DIR, WORK_DIR
from src.date_navigation import parse_iso_date, validate_date_range
from src.excel_writer import write_excel
from src.logging_utils import sanitize, setup_logging
from src.regional_collector import RegionalCollector
from src.regions import (
    is_all_regions,
    parse_cli_regions,
    print_region_confirmation,
    prompt_regions,
    resume_checkpoint_path,
)


def prompt_date(label: str) -> str:
    while True:
        value = input(f"{label}을 입력하세요 (YYYY-MM-DD): ").strip()
        try:
            parse_iso_date(value)
            return value
        except ValueError as exc:
            print(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="빅카인즈 지역이슈 뉴스 링크 점검 및 Excel 보고서 생성")
    parser.add_argument("--start-date", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--regions", help="전체 또는 쉼표로 구분한 정확한 지역명. 생략 시 선택 메뉴 표시")
    parser.add_argument("--headed", action="store_true", help="디버깅용으로 브라우저 표시")
    parser.add_argument("--resume", action="store_true", help="체크포인트에서 재개")
    parser.add_argument("--max-issues", type=int, help="지역별 최대 이슈 수(테스트용)")
    parser.add_argument("--timeout", type=int, default=30, help="링크 제한시간(초, 기본 30)")
    parser.add_argument("--retries", type=int, default=2, choices=range(0, 3), help="실패 재시도 횟수(기본 2)")
    parser.add_argument("--link-delay", type=float, default=0.5, help="링크 사이 대기시간(초)")
    parser.add_argument("--debug", action="store_true", help="추가 디버그 모드")
    return parser


def resolve_dates(args, parser) -> tuple[str, str]:
    interactive = args.start_date is None and args.end_date is None
    start = args.start_date or prompt_date("시작일")
    end = args.end_date or prompt_date("종료일")
    try:
        validate_date_range(start, end)
    except ValueError as exc:
        if interactive:
            print(exc)
            return resolve_dates(argparse.Namespace(start_date=None, end_date=None), parser)
        parser.error(str(exc))
    return start, end


def resolve_regions(args, parser) -> list[str]:
    if args.regions is None:
        return prompt_regions()
    try:
        regions = parse_cli_regions(args.regions)
    except ValueError as exc:
        parser.error(str(exc))
    print_region_confirmation(regions)
    return regions


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_issues is not None and args.max_issues < 1:
        parser.error("--max-issues는 1 이상이어야 합니다.")
    start_text, end_text = resolve_dates(args, parser)
    start_date, end_date = validate_date_range(start_text, end_text)
    regions = resolve_regions(args, parser)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    started_at = datetime.now().astimezone()
    logger = setup_logging(WORK_DIR / f"audit_{start_text}_{end_text}_{run_stamp}.log")
    selected_checkpoint = resume_checkpoint_path(
        WORK_DIR, start_text, end_text, regions, resume=args.resume,
    )
    checkpoint_config = {
        "start_date": start_text,
        "end_date": end_text,
        "regions": regions,
        "selection_mode": "전체" if is_all_regions(regions) else "선택",
    }
    try:
        checkpoint = CheckpointStore(
            selected_checkpoint, resume=args.resume, run_config=checkpoint_config,
        )
    except ValueError as exc:
        parser.error(str(exc))

    collector = RegionalCollector(
        start_date=start_date, end_date=end_date, regions=regions, headed=args.headed,
        max_issues=args.max_issues, timeout_ms=args.timeout * 1000, retries=args.retries,
        link_delay_ms=max(0, int(args.link_delay * 1000)), checkpoint=checkpoint,
        logger=logger, debug=args.debug,
    )
    interrupted = False
    failed = False
    try:
        asyncio.run(collector.run())
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("사용자 중단: 현재까지 수집한 결과를 저장합니다.")
    except Exception as exc:
        failed = True
        logger.error("실행 중 오류가 발생했지만 현재까지 결과를 저장합니다: %s", sanitize(exc))

    ended_at = datetime.now().astimezone()
    output_path = OUTPUT_DIR / f"bigkinds_regional_link_audit_{start_text}_{end_text}_{run_stamp}.xlsx"
    completed_regions = len({region for _, region in checkpoint.completed})
    excel_path = write_excel(
        output_path, checkpoint.rows, checkpoint.debug_entries,
        start_date=start_text, end_date=end_text, started_at=started_at, ended_at=ended_at,
        region_count=completed_regions, issue_count=len(checkpoint.issue_keys),
        selected_regions=regions,
    )
    print(f"Excel 파일: {excel_path}")
    if interrupted:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
