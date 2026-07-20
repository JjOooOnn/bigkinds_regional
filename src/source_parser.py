from __future__ import annotations

import re

from .models import SourceInfo

DATE_RE = re.compile(r"\b(\d{4}[-.]\d{2}[-.]\d{2})\b")


def parse_source_text(text: str, card_order: int = 0) -> SourceInfo:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines() if line.strip()]
    source_type = lines[0] if lines and lines[0] in {"뉴스", "공지사항"} else ""
    meta_index = next((i for i, line in enumerate(lines) if DATE_RE.search(line)), None)
    publisher, article_date = "", ""
    if meta_index is not None:
        meta = lines[meta_index]
        match = DATE_RE.search(meta)
        article_date = match.group(1).replace(".", "-") if match else ""
        publisher = meta[: match.start()].rstrip(" |") if match else meta
    title_start = (meta_index + 1) if meta_index is not None else (1 if source_type else 0)
    title = " ".join(lines[title_start:]).strip()
    return SourceInfo(source_type, publisher, article_date, title, card_order)

