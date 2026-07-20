from __future__ import annotations

import argparse
import sys

from src.application.audit_service import AuditRequest, AuditService
from src.date_navigation import parse_iso_date, validate_date_range
from src.regions import (
    parse_cli_regions,
    print_region_confirmation,
    prompt_regions,
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
    validate_date_range(start_text, end_text)
    regions = resolve_regions(args, parser)
    try:
        result = AuditService().run(
            AuditRequest(
                start_date=start_text,
                end_date=end_text,
                regions=regions,
                headed=args.headed,
                resume=args.resume,
                max_issues=args.max_issues,
                timeout_seconds=args.timeout,
                retries=args.retries,
                link_delay_seconds=args.link_delay,
                debug=args.debug,
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    if result.excel_path:
        print(f"Excel 파일: {result.excel_path}")
    if result.status == "cancelled":
        return 130
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
