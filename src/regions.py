from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path


# 지역명과 표시 순서는 이 모듈에서만 관리한다. 사이트 조작 시에는 이 값을
# 직접 클릭하지 않고, 페이지에서 읽은 option과 exact match로 검증한 뒤 사용한다.
REGION_DISPLAY_ORDER: tuple[str, ...] = (
    "서울특별시",
    "부산광역시",
    "대구광역시",
    "인천광역시",
    "광주광역시",
    "대전광역시",
    "울산광역시",
    "세종특별자치시",
    "경기도",
    "강원특별자치도",
    "충청북도",
    "충청남도",
    "전북특별자치도",
    "전라남도",
    "경상북도",
    "경상남도",
    "제주특별자치도",
)
SUPPORTED_REGIONS = frozenset(REGION_DISPLAY_ORDER)


def _deduplicate(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def parse_region_numbers(value: str) -> list[str]:
    raw = value.strip()
    if not raw:
        raise ValueError("지역 번호를 입력해야 합니다.")
    parts = [part.strip() for part in raw.split(",")]
    if any(not part or not part.isdecimal() for part in parts):
        raise ValueError("지역 번호는 0부터 17까지의 숫자를 쉼표로 구분해 입력하세요.")
    numbers = [int(part) for part in parts]
    invalid = [number for number in numbers if not 0 <= number <= len(REGION_DISPLAY_ORDER)]
    if invalid:
        raise ValueError(f"유효하지 않은 지역 번호입니다: {', '.join(map(str, invalid))}")
    if 0 in numbers and any(number != 0 for number in numbers):
        raise ValueError("전체는 다른 지역과 함께 선택할 수 없습니다.")
    if 0 in numbers:
        return list(REGION_DISPLAY_ORDER)
    return _deduplicate([REGION_DISPLAY_ORDER[number - 1] for number in numbers])


def prompt_regions(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> list[str]:
    output_fn("\n점검할 지역을 선택하세요.")
    output_fn("0. 전체")
    for number, region in enumerate(REGION_DISPLAY_ORDER, 1):
        output_fn(f"{number}. {region}")
    while True:
        try:
            selected = parse_region_numbers(input_fn("지역 번호를 입력하세요: "))
        except ValueError as exc:
            output_fn(str(exc))
            continue
        print_region_confirmation(selected, output_fn)
        return selected


def parse_cli_regions(value: str) -> list[str]:
    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise ValueError("--regions에는 빈 지역명을 입력할 수 없습니다.")
    parts = _deduplicate(parts)
    if "전체" in parts and len(parts) > 1:
        raise ValueError("전체는 다른 지역과 함께 선택할 수 없습니다.")
    if parts == ["전체"]:
        return list(REGION_DISPLAY_ORDER)
    unknown = [name for name in parts if name not in SUPPORTED_REGIONS]
    if unknown:
        raise ValueError(f"존재하지 않는 지역명입니다: {', '.join(unknown)}")
    return parts


def validate_site_regions(site_regions: Sequence[str], selected_regions: Sequence[str]) -> list[str]:
    """사이트 option 전체와 선택 지역을 exact match로 검증한다."""
    actual = [name.strip() for name in site_regions if name.strip()]
    if len(actual) != len(REGION_DISPLAY_ORDER):
        raise ValueError(
            f"사이트에서 확인된 지역 option은 {len(actual)}개입니다. "
            f"예상한 {len(REGION_DISPLAY_ORDER)}개와 달라 실행을 중단합니다."
        )
    if len(set(actual)) != len(actual):
        raise ValueError("사이트 지역 option에 중복된 표시 명칭이 있어 실행을 중단합니다.")
    unavailable = [name for name in selected_regions if name not in actual]
    if unavailable:
        raise ValueError(
            f"선택한 지역 '{unavailable[0]}'에 해당하는 사이트 option을 찾지 못했습니다."
        )
    missing = [name for name in REGION_DISPLAY_ORDER if name not in actual]
    unexpected = [name for name in actual if name not in SUPPORTED_REGIONS]
    if missing or unexpected:
        raise ValueError(
            "사이트 지역 option의 표시 명칭이 프로그램 목록과 정확히 일치하지 않습니다. "
            f"누락={missing or '없음'}, 예상 밖={unexpected or '없음'}"
        )
    return list(selected_regions)


def is_all_regions(regions: Sequence[str]) -> bool:
    return list(regions) == list(REGION_DISPLAY_ORDER)


def print_region_confirmation(
    regions: Sequence[str], output_fn: Callable[[str], None] = print,
) -> None:
    output_fn("선택 지역: 전체" if is_all_regions(regions) else f"선택 지역: {', '.join(regions)}")
    output_fn(f"총 {len(regions)}개 지역을 점검합니다.")


def checkpoint_scope(regions: Sequence[str]) -> str:
    if is_all_regions(regions):
        return "all"
    serialized = json.dumps(list(regions), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return f"selected_{digest}"


def checkpoint_path(work_dir: Path, start_date: str, end_date: str, regions: Sequence[str]) -> Path:
    return work_dir / f"checkpoint_{start_date}_{end_date}_{checkpoint_scope(regions)}.jsonl"


def resume_checkpoint_path(
    work_dir: Path, start_date: str, end_date: str, regions: Sequence[str], resume: bool,
) -> Path:
    """정확한 파일을 우선하고, 불일치 시 비교할 가장 가까운 체크포인트를 찾는다."""
    exact = checkpoint_path(work_dir, start_date, end_date, regions)
    if not resume or exact.exists():
        return exact
    same_dates = list(work_dir.glob(f"checkpoint_{start_date}_{end_date}_*.jsonl"))
    if same_dates:
        return max(same_dates, key=lambda path: path.stat().st_mtime)
    scope = checkpoint_scope(regions)
    same_regions = list(work_dir.glob(f"checkpoint_*_*_{scope}.jsonl"))
    if same_regions:
        return max(same_regions, key=lambda path: path.stat().st_mtime)
    return exact
