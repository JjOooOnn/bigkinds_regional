from __future__ import annotations

import argparse
import logging

import pytest

import main
from src.checkpoint import CheckpointStore
from src.regions import (
    REGION_DISPLAY_ORDER,
    checkpoint_path,
    parse_cli_regions,
    parse_region_numbers,
    prompt_regions,
    resume_checkpoint_path,
    validate_site_regions,
)
from src.regional_collector import RegionalCollector


def _prompt(*answers: str):
    iterator = iter(answers)
    output: list[str] = []
    selected = prompt_regions(lambda _prompt: next(iterator), output.append)
    return selected, output


def test_zero_selects_all_17_regions():
    assert parse_region_numbers("0") == list(REGION_DISPLAY_ORDER)


def test_single_number_selection():
    assert parse_region_numbers("9") == ["경기도"]


def test_multiple_number_selection_keeps_input_order():
    assert parse_region_numbers("17,1,9") == ["제주특별자치도", "서울특별시", "경기도"]


def test_spaces_between_numbers_are_ignored():
    assert parse_region_numbers("1, 5, 17") == ["서울특별시", "광주광역시", "제주특별자치도"]


def test_duplicate_numbers_are_removed():
    assert parse_region_numbers("1,2,1,2,9") == ["서울특별시", "부산광역시", "경기도"]


def test_invalid_number_prompts_again():
    selected, output = _prompt("18", "2")
    assert selected == ["부산광역시"]
    assert any("유효하지 않은" in line for line in output)


def test_empty_input_prompts_again():
    selected, output = _prompt("", "1")
    assert selected == ["서울특별시"]
    assert any("입력해야" in line for line in output)


def test_zero_with_individual_number_prompts_again():
    selected, output = _prompt("0,1", "1,2")
    assert selected == ["서울특별시", "부산광역시"]
    assert "전체는 다른 지역과 함께 선택할 수 없습니다." in output


def test_cli_all_regions():
    assert parse_cli_regions("전체") == list(REGION_DISPLAY_ORDER)


def test_cli_single_region():
    assert parse_cli_regions(" 서울특별시 ") == ["서울특별시"]


def test_cli_multiple_regions_deduplicates_and_keeps_order():
    assert parse_cli_regions("경기도, 서울특별시,경기도") == ["경기도", "서울특별시"]


def test_cli_rejects_unknown_region_and_all_mixed_with_region():
    with pytest.raises(ValueError, match="존재하지 않는 지역명"):
        parse_cli_regions("서울시")
    with pytest.raises(ValueError, match="함께 선택할 수 없습니다"):
        parse_cli_regions("전체,서울특별시")


def test_cli_argument_does_not_show_interactive_menu(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("input() must not be called"))
    args = argparse.Namespace(regions="서울특별시,부산광역시")
    assert main.resolve_regions(args, main.build_parser()) == ["서울특별시", "부산광역시"]


def test_site_options_and_selected_names_are_exactly_compared():
    selected = ["강원특별자치도", "서울특별시"]
    assert validate_site_regions(REGION_DISPLAY_ORDER, selected) == selected
    changed = list(REGION_DISPLAY_ORDER)
    changed[9] = "강원도"
    with pytest.raises(ValueError, match="강원특별자치도.*찾지 못했습니다"):
        validate_site_regions(changed, selected)


def test_site_option_count_other_than_17_is_rejected():
    with pytest.raises(ValueError, match="16개"):
        validate_site_regions(REGION_DISPLAY_ORDER[:-1], ["서울특별시"])


def test_unselected_regions_are_not_returned_for_execution():
    selected = ["경기도", "부산광역시"]
    collector = object.__new__(RegionalCollector)
    collector.requested_regions = selected
    collector.logger = logging.getLogger("test_regions")
    assert collector._select_requested_regions(list(REGION_DISPLAY_ORDER)) == selected


def test_checkpoint_region_mismatch_is_detected_and_scopes_are_separate(tmp_path):
    all_regions = list(REGION_DISPLAY_ORDER)
    all_path = checkpoint_path(tmp_path, "2026-07-01", "2026-07-16", all_regions)
    selected_path = checkpoint_path(tmp_path, "2026-07-01", "2026-07-16", ["서울특별시"])
    assert all_path != selected_path
    stored = {
        "start_date": "2026-07-01", "end_date": "2026-07-16",
        "regions": all_regions, "selection_mode": "전체",
    }
    CheckpointStore(all_path, run_config=stored)
    assert resume_checkpoint_path(
        tmp_path, "2026-07-01", "2026-07-16", ["서울특별시"], resume=True,
    ) == all_path
    current = {**stored, "regions": ["서울특별시"], "selection_mode": "선택"}
    with pytest.raises(ValueError, match="선택 지역.*다릅니다") as exc_info:
        CheckpointStore(all_path, resume=True, run_config=current)
    assert "기존: 전체" in str(exc_info.value)
    assert "현재: 서울특별시" in str(exc_info.value)


def test_existing_all_region_automatic_iteration_remains_available():
    selected = parse_region_numbers("0")
    assert validate_site_regions(REGION_DISPLAY_ORDER, selected) == list(REGION_DISPLAY_ORDER)
