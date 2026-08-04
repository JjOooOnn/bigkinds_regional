from __future__ import annotations

import logging
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

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
SECRET_KEY_PATTERN = (
    r"(?:access_token|api_?key|apikey|auth|authorization|cookie|jwt|password|"
    r"secret|session(?:id)?|sid|token)"
)
QUOTED_SECRET_VALUE_PATTERN = re.compile(
    rf'''(?ix)
    (?P<prefix>["']?{SECRET_KEY_PATTERN}["']?\s*[:=]\s*)
    (?P<quote>["'])
    (?:\\.|(?!(?P=quote)).)*
    (?P=quote)
    '''
)
GENERIC_SECRET_VALUE_PATTERN = re.compile(
    r'''(?ix)
    (
        ["']?
        (?:access_token|api_?key|apikey|auth|authorization|cookie|jwt|password|
           secret|session(?:id)?|sid|token)
        ["']?\s*[:=]\s*
    )
    [^"'\s,;&#}]+
    '''
)
BEARER_TOKEN_PATTERN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def sanitize(value: object) -> str:
    text = str(value or "")
    text = QUOTED_SECRET_VALUE_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"[마스킹]{match.group('quote')}"
        ),
        text,
    )
    text = GENERIC_SECRET_VALUE_PATTERN.sub(r"\1[마스킹]", text)
    text = BEARER_TOKEN_PATTERN.sub(r"\1[마스킹]", text)
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


def configure_lifecycle_logging(stream: TextIO | None = None) -> logging.Logger:
    logger = logging.getLogger("bigkinds_lifecycle")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def log_lifecycle_event(
    logger: logging.Logger,
    component: str,
    event: str,
    **details: object,
) -> None:
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "component": sanitize(component),
        "event": sanitize(event),
        **{key: sanitize(value) for key, value in details.items()},
    }
    logger.info("LIFECYCLE %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


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
            "click_after_url", "first_opened_url", "inferred_url", "final_url", "inspection_url",
            "access_reason_code", "detected_phrase", "detected_locator", "detected_dom_area",
            "document_title", "visible_h1", "matched_title", "content_container_locator",
            "initial_document_title", "rechecked_document_title", "initial_verdict",
            "rechecked_verdict",
        }
        else value
        for key, value in kwargs.items()
    }
    return DebugEntry(timestamp=datetime.now().astimezone().isoformat(timespec="seconds"), stage=stage, **cleaned)
