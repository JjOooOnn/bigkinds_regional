from __future__ import annotations

import json
from pathlib import Path

from .models import AuditRow, DebugEntry
from .url_utils import deduplicate_rows


class CheckpointStore:
    def __init__(self, path: Path, resume: bool = False, run_config: dict | None = None):
        self.path = path
        self.run_config = run_config
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not resume:
            self.path.write_text("", encoding="utf-8")
        elif not self.path.exists():
            raise ValueError(f"재개할 체크포인트 파일을 찾지 못했습니다: {self.path}")
        self.rows: list[AuditRow] = []
        self.debug_entries: list[DebugEntry] = []
        self.completed: set[tuple[str, str]] = set()
        self.issue_keys: set[tuple[str, str, int]] = set()
        self.stored_run_config: dict | None = None
        if resume:
            self._load()
            self._validate_run_config()
        elif self.run_config is not None:
            self._append({"type": "run_config", "data": self.run_config})

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                item_type = item.get("type")
                if item_type == "run_config":
                    self.stored_run_config = item["data"]
                elif item_type == "row":
                    self.rows.append(AuditRow.from_dict(item["data"]))
                elif item_type == "debug":
                    self.debug_entries.append(DebugEntry(**item["data"]))
                elif item_type == "completed":
                    self.completed.add((item["requested_date"], item["region"]))
                elif item_type == "issue":
                    self.issue_keys.add((item["requested_date"], item["region"], int(item["issue_order"])))
            except (ValueError, TypeError, KeyError):
                continue
        self.rows = deduplicate_rows(self.rows)
        self.issue_keys.update((row.requested_date, row.region, row.issue_order) for row in self.rows)

    def _validate_run_config(self) -> None:
        if self.run_config is None:
            return
        if self.stored_run_config is None:
            raise ValueError("기존 체크포인트에 실행 조건이 없어 안전하게 재개할 수 없습니다.")
        labels = {"start_date": "시작일", "end_date": "종료일", "regions": "선택 지역"}
        for key in ("start_date", "end_date", "regions"):
            stored_value = self.stored_run_config.get(key)
            current_value = self.run_config.get(key)
            if stored_value == current_value:
                continue
            if key == "regions":
                stored_value = self._region_label(self.stored_run_config)
                current_value = self._region_label(self.run_config)
            raise ValueError(
                f"기존 체크포인트의 {labels[key]}과 현재 {labels[key]}이 다릅니다.\n"
                f"기존: {stored_value}\n현재: {current_value}"
            )

    @staticmethod
    def _region_label(config: dict) -> str:
        if config.get("selection_mode") == "전체":
            return "전체"
        return ", ".join(config.get("regions") or [])

    def _append(self, item: dict) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            stream.flush()

    def add_row(self, row: AuditRow) -> bool:
        before = len(self.rows)
        combined = deduplicate_rows([*self.rows, row])
        if len(combined) == before:
            return False
        self.rows = combined
        self._append({"type": "row", "data": row.to_dict()})
        return True

    def add_debug(self, entry: DebugEntry) -> None:
        self.debug_entries.append(entry)
        self._append({"type": "debug", "data": entry.to_dict()})

    def mark_issue(self, requested_date: str, region: str, issue_order: int) -> None:
        key = (requested_date, region, issue_order)
        if key not in self.issue_keys:
            self.issue_keys.add(key)
            self._append({
                "type": "issue", "requested_date": requested_date,
                "region": region, "issue_order": issue_order,
            })

    def mark_completed(self, requested_date: str, region: str) -> None:
        key = (requested_date, region)
        if key not in self.completed:
            self.completed.add(key)
            self._append({"type": "completed", "requested_date": requested_date, "region": region})
