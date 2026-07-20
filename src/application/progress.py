from __future__ import annotations

from typing import Any, Protocol


class AuditCancelled(Exception):
    """안전한 경계에서 사용자의 중단 요청을 전달한다."""


class ProgressReporter(Protocol):
    def emit(self, event: str, message: str = "", **data: Any) -> None:
        """진행 이벤트를 전달한다."""


class CancellationToken(Protocol):
    def is_cancel_requested(self) -> bool:
        """새 작업을 시작하지 않아야 하면 True를 반환한다."""


class NullProgressReporter:
    def emit(self, event: str, message: str = "", **data: Any) -> None:
        return None


class NeverCancelToken:
    def is_cancel_requested(self) -> bool:
        return False

