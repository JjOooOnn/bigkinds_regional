from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SourceInfo:
    source_type: str = ""
    publisher: str = ""
    article_date: str = ""
    title: str = ""
    card_order: int = 0


@dataclass
class AuditRow:
    requested_date: str
    displayed_date: str
    region: str
    issue_order: int
    issue_title: str
    issue_categories: str
    source_count: int
    source_type: str
    publisher: str
    article_date: str
    article_title: str
    original_url: str
    final_url: str
    http_status: int | None
    browser_result: str
    link_working_yn: str
    verdict: str
    response_seconds: float
    error_message: str
    checked_at: str
    region_order: int = field(default=0, repr=False)
    source_order: int = field(default=0, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditRow":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class DebugEntry:
    timestamp: str
    stage: str
    requested_date: str = ""
    displayed_date: str = ""
    region: str = ""
    issue_order: int | str = ""
    issue_title: str = ""
    source_href_raw: str = ""
    source_href_property: str = ""
    click_target_raw: str = ""
    normalization_input: str = ""
    original_url: str = ""
    normalization_method: str = ""
    click_before_url: str = ""
    click_after_url: str = ""
    first_opened_url: str = ""
    new_tab_yn: str = ""
    current_tab_moved_yn: str = ""
    inferred_url: str = ""
    url_structure_anomaly_yn: str = ""
    url_structure_anomaly_details: str = ""
    locator: str = ""
    event: str = ""
    exception_type: str = ""
    details: str = ""
    http_status: int | str = ""
    final_url: str = ""
    screenshot_path: str = ""
    retry_count: int = 0
    access_reason_code: str = ""
    detected_phrase: str = ""
    detected_locator: str = ""
    detected_dom_area: str = ""
    detected_visible_yn: str = ""
    document_title: str = ""
    visible_h1: str = ""
    article_exists_yn: str = ""
    primary_text_length: int | str = ""
    article_title_match_yn: str = ""
    article_rendered_yn: str = ""
    non_news_title_match_yn: str = ""
    non_news_content_rendered_yn: str = ""
    matched_title: str = ""
    content_container_locator: str = ""
    attachment_exists_yn: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
