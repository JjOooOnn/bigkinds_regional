from __future__ import annotations

import pytest

from scripts.check_commit_message import CommitMessageError, extract_subject, validate_commit_message


@pytest.mark.parametrize(
    "subject",
    [
        "[v1.0.1] feat: 버전 표시 추가",
        "[v1.0.1] fix: Target crashed 복구",
        "[v1.0.1] docs: 커밋 규칙 문서화",
        "[v1.0.1] test: 커밋 제목 검사 추가",
        "[v1.0.1] refactor: 버전 조회 경로 정리",
        "[v1.0.1] chore: 패키지 버전 갱신",
    ],
)
def test_validate_commit_message_accepts_current_version_and_allowed_type(subject):
    validate_commit_message(subject, version="1.0.1")


@pytest.mark.parametrize(
    "subject",
    [
        "fix: 버전 표기 누락",
        "[v1.0.0] fix: 이전 버전",
        "[v1.0.1] style: 허용되지 않는 type",
        "[v1.0.1] fix:",
    ],
)
def test_validate_commit_message_rejects_invalid_subject(subject):
    with pytest.raises(CommitMessageError):
        validate_commit_message(subject, version="1.0.1")


def test_extract_subject_skips_comments_and_blank_lines():
    assert extract_subject("\n# 안내\n[v1.0.1] fix: 제목\n\n본문") == "[v1.0.1] fix: 제목"


def test_extract_subject_accepts_utf8_bom():
    assert extract_subject("\ufeff[v1.0.1] fix: 제목") == "[v1.0.1] fix: 제목"
