from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.version import read_app_version


ALLOWED_TYPES = ("feat", "fix", "docs", "test", "refactor", "chore")


class CommitMessageError(ValueError):
    """Raised when a commit subject does not follow the project convention."""


def extract_subject(message: str) -> str:
    for line in message.splitlines():
        subject = line.strip().lstrip("\ufeff")
        if subject and not subject.startswith("#"):
            return subject
    return ""


def validate_commit_subject(subject: str, *, version: str | None = None) -> None:
    expected_version = version or read_app_version()
    expected_prefix = f"[v{expected_version}]"
    prefix, separator, remainder = subject.partition(" ")
    if prefix != expected_prefix or not separator:
        raise CommitMessageError(
            f"커밋 제목은 '{expected_prefix} type: 요약' 형식이어야 합니다."
        )

    commit_type, type_separator, summary = remainder.partition(": ")
    if commit_type not in ALLOWED_TYPES or not type_separator or not summary.strip():
        allowed = ", ".join(ALLOWED_TYPES)
        raise CommitMessageError(
            f"커밋 type은 {allowed} 중 하나여야 합니다. "
            f"예: '{expected_prefix} fix: Target crashed 복구'"
        )


def validate_commit_message(message: str, *, version: str | None = None) -> None:
    validate_commit_subject(extract_subject(message), version=version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="커밋 제목의 제품 버전 표기를 검사합니다.")
    parser.add_argument("message_file", type=Path, help="Git이 전달한 커밋 메시지 파일")
    args = parser.parse_args(argv)

    try:
        validate_commit_message(args.message_file.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, CommitMessageError) as exc:
        parser.exit(1, f"커밋 제목 검사 실패: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
