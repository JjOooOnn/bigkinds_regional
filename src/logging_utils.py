from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from .models import DebugEntry

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(https?://)[^/\s:@]+:[^@/\s]+@"),
    re.compile(
        r"(?i)([?&](?:access_token|api_?key|apikey|auth|authorization|cookie|jwt|password|"
        r"session(?:id)?|sid|token)=)[^&#\s]+"
    ),
    re.compile(r"(?i)(cookie|set-cookie|authorization)\s*[:=]\s*[^\r\n]+"),
    re.compile(r"(?i)(session(?:id)?|token)\s*[:=]\s*[^\s;,&]+"),
]


def sanitize(value: object) -> str:
    text = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        def replacement(match):
            prefix = match.group(1)
            if prefix.lower().startswith(("http://", "https://")):
                return f"{prefix}[마스킹]@"
            if prefix.startswith(("?", "&")):
                return f"{prefix}[마스킹]"
            return f"{prefix}=[마스킹]"
        text = pattern.sub(replacement, text)
    return text[:8000]


def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bigkinds_audit")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def debug_entry(stage: str, **kwargs) -> DebugEntry:
    cleaned = {
        key: sanitize(value)
        if key in {
            "details", "locator", "event", "source_href_raw", "source_href_property",
            "click_target_raw", "normalization_input", "original_url", "click_before_url",
            "click_after_url", "first_opened_url", "inferred_url", "final_url",
            "access_reason_code", "detected_phrase", "detected_locator", "detected_dom_area",
            "document_title", "visible_h1", "matched_title", "content_container_locator",
        }
        else value
        for key, value in kwargs.items()
    }
    return DebugEntry(timestamp=datetime.now().astimezone().isoformat(timespec="seconds"), stage=stage, **cleaned)
