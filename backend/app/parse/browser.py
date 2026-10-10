"""Headless Chromium via Playwright for pages that need JavaScript (DECISIONS D-025).

Every request the page makes passes the same public-address check as plain fetches; WebSockets,
service workers and downloads are blocked.
"""

from __future__ import annotations

import logging
from typing import Any

from app.security.addresses import BlockedAddressError, Resolver, check_url, system_resolver

logger = logging.getLogger("trustbook.browser")

GOTO_TIMEOUT_MS = 30_000


class PlaywrightRenderer:
    """Renders a URL and returns the page HTML (sync API; runs in a worker thread)."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolver = resolver

    def _allowed(self, url: str, cache: dict[str, bool]) -> bool:
        if url in cache:
            return cache[url]
        try:
            check_url(url, self._resolver)
            allowed = True
        except (BlockedAddressError, OSError):
            allowed = False
        cache[url] = allowed
        return allowed

    def render(self, url: str) -> str:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        cache: dict[str, bool] = {}

        def guard(route: Any, request: Any) -> None:
            if self._allowed(request.url, cache):
                route.continue_()
            else:
                route.abort()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                context.route("**/*", guard)
                context.route_web_socket("**/*", lambda _ws: None)  # never connects
                page = context.new_page()
                try:
                    page.goto(url, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
                except PlaywrightError:
                    logger.info("Page did not settle in time; using what loaded")
                return str(page.content())
            finally:
                browser.close()
