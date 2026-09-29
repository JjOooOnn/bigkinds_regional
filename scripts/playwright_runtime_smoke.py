"""Check the installed Playwright Chromium runtime without external network access."""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from src.browser_runtime import chromium_launch_options, managed_display
from src.config import load_runtime_config


async def run_smoke_check() -> None:
    runtime = load_runtime_config()
    with managed_display(runtime):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                **chromium_launch_options(headed=False, runtime=runtime)
            )
            context = None
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(
                    "data:text/html,<main id='runtime-smoke'>Chromium ready</main>",
                    wait_until="domcontentloaded",
                )
                value = await page.locator("#runtime-smoke").inner_text()
                if value != "Chromium ready":
                    raise RuntimeError(f"Chromium DOM smoke check returned unexpected text: {value!r}")
            finally:
                try:
                    if context is not None:
                        await context.close()
                finally:
                    await browser.close()


def main() -> int:
    asyncio.run(run_smoke_check())
    print("Playwright Chromium runtime smoke check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
