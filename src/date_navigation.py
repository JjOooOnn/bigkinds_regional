from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Iterable

from playwright.async_api import Page

DISPLAYED_DATE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")


def parse_iso_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력하세요.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("존재하지 않는 날짜입니다. YYYY-MM-DD 형식으로 입력하세요.") from exc


def validate_date_range(start: str, end: str) -> tuple[date, date]:
    start_date, end_date = parse_iso_date(start), parse_iso_date(end)
    if end_date < start_date:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
    return start_date, end_date


def inclusive_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


class DateNavigator:
    def __init__(self, page: Page, timeout_ms: int = 30_000):
        self.page = page
        self.timeout_ms = timeout_ms

    async def displayed_date(self) -> date | None:
        previous = self.page.get_by_role("button", name="이전 날짜")
        try:
            text = await previous.evaluate("e => e.parentElement.innerText")
        except Exception:
            text = await self.page.locator("body").inner_text()
        match = DISPLAYED_DATE_RE.search(text)
        return date(*map(int, match.groups())) if match else None

    async def move_to(self, target: date, retries: int = 1) -> tuple[bool, date | None]:
        for _ in range(retries + 1):
            current = await self.displayed_date()
            if current == target:
                return True, current
            if current is None:
                continue
            step = 1 if target > current else -1
            button_name = "다음 날짜" if step > 0 else "이전 날짜"
            while current != target:
                button = self.page.get_by_role("button", name=button_name)
                if await button.is_disabled():
                    break
                before = current
                before_text = await button.evaluate("e => e.parentElement.innerText")
                await button.click()
                try:
                    await self.page.wait_for_function(
                        "([label, old]) => { const b=document.querySelector(`button[aria-label='${label}']`); "
                        "return b && b.parentElement && b.parentElement.innerText !== old; }",
                        arg=[button_name, before_text],
                        timeout=self.timeout_ms,
                    )
                except Exception:
                    pass
                await self.page.wait_for_timeout(350)
                current = await self.displayed_date()
                if current is None or current == before:
                    break
                step = 1 if target > current else -1
                button_name = "다음 날짜" if step > 0 else "이전 날짜"
            if current == target:
                return True, current
            await self.page.wait_for_timeout(500)
        return False, await self.displayed_date()
