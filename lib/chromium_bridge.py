"""Chromium login bridge: obtains a browser session and hands it to instagrapi."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
from settings import CHROMIUM_COOKIES_FILE, CHROMIUM_EXECUTABLE, CHROMIUM_PROFILE_DIR


class ChromiumBridge:
    def __init__(self) -> None:
        self.profile_dir = CHROMIUM_PROFILE_DIR
        self.cookies_file = CHROMIUM_COOKIES_FILE

    @staticmethod
    def _session_id(cookies: list[dict]) -> str | None:
        return next((c.get("value") for c in cookies if c.get("name") == "sessionid"), None)

    def _save_cookies(self, cookies: list[dict]) -> None:
        temp_file = self.cookies_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        temp_file.chmod(0o600)
        temp_file.replace(self.cookies_file)

    def saved_session_id(self) -> str | None:
        if not self.cookies_file.exists():
            return None
        try:
            cookies = json.loads(self.cookies_file.read_text(encoding="utf-8"))
            return self._session_id(cookies)
        except (OSError, json.JSONDecodeError):
            return None

    def login(self, keep_open: bool = False, force_refresh: bool = False) -> str:
        """Authenticate in Chromium and return the session cookie.

        Manual profile setup uses ``keep_open=True`` and saves cookies until the
        user closes Chromium. Automatic recovery returns as soon as login works.
        """
        config.validate_credentials()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            context = None
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    executable_path=str(CHROMIUM_EXECUTABLE) if CHROMIUM_EXECUTABLE else None,
                    headless=config.CHROMIUM_HEADLESS,
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                )
                page = context.pages[0] if context.pages else context.new_page()
                session_id: str | None = None

                if force_refresh:
                    context.clear_cookies()
                    if self.cookies_file.exists():
                        self.cookies_file.unlink(missing_ok=True)

                page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=60_000)
                cookies = context.cookies("https://www.instagram.com")
                session_id = self._session_id(cookies)

                if not session_id:
                    username = page.locator('input[name="username"]')
                    password = page.locator('input[name="password"]')
                    try:
                        username.wait_for(state="visible", timeout=30_000)
                        username.fill(config.USERNAME)
                        password.fill(config.PASSWORD)
                        page.locator('button[type="submit"]').click()
                    except PlaywrightTimeoutError:
                        pass

                if keep_open:
                    print("Chromium will stay open. Finish setup, then close the browser window to save the profile.")
                elif not config.CHROMIUM_HEADLESS:
                    print("Complete any Instagram verification in the Chromium window.")

                deadline = time.monotonic() + config.LOGIN_TIMEOUT_SECONDS
                last_continue_click = 0.0
                while True:
                    try:
                        if context.pages:
                            page = context.pages[0]
                        else:
                            if keep_open:
                                break
                            page = context.new_page()

                        # Check for active 2FA or verification challenges
                        current_url = page.url.lower()
                        is_challenge = any(k in current_url for k in ("/challenge", "/two_factor", "/checkpoint", "/terms/unblock"))
                        if is_challenge and config.CHROMIUM_HEADLESS:
                            raise RuntimeError("Instagram challenge_required: 2FA or verification checkpoint encountered in headless mode")

                        cookies = context.cookies("https://www.instagram.com")
                        current_session_id = self._session_id(cookies)
                        if current_session_id:
                            session_id = current_session_id
                            self._save_cookies(cookies)
                            if not keep_open:
                                return session_id
                        elif time.monotonic() - last_continue_click >= 3:
                            continue_button = page.get_by_role("button", name=re.compile(r"^continue(?: as .*)?$", re.I)).first
                            if continue_button.count() and continue_button.is_visible():
                                continue_button.click(timeout=2_000)
                                last_continue_click = time.monotonic()

                        if keep_open and not context.pages:
                            break
                        if not session_id and time.monotonic() >= deadline:
                            raise RuntimeError("Chromium login timed out before Instagram created a session (challenge_required)")
                        page.wait_for_timeout(1_000)
                    except PlaywrightError as err:
                        if "closed" in str(err).lower() or not context.pages:
                            if keep_open or session_id:
                                break
                            raise RuntimeError(f"Chromium closed before Instagram login completed: {err}")
                        time.sleep(1.0)

                if not session_id:
                    raise RuntimeError("Chromium closed before Instagram login completed (login_required)")
                return session_id
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
