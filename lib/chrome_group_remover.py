"""Permission-gated Instagram group-member removal through the saved Chromium UI."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
from settings import CHROMIUM_COOKIES_FILE, CHROMIUM_EXECUTABLE, CHROMIUM_PROFILE_DIR

LOGGER = logging.getLogger("ineffa.chrome-removal")


class ChromeGroupRemover:
    """Click Instagram's own group-member Remove controls using the saved profile."""

    def __init__(self, profile_dir: Path | None = None) -> None:
        self.profile_dir = profile_dir or CHROMIUM_PROFILE_DIR
        self.lock = threading.Lock()

    @staticmethod
    def _is_login_or_challenge(url: str) -> bool:
        url_lower = url.lower()
        return any(k in url_lower for k in (
            "/accounts/login", "/challenge", "/two_factor", "/checkpoint",
            "/accounts/onetap", "/terms/unblock", "/accounts/suspended",
        ))

    @staticmethod
    def _open_context(playwright):
        browser = playwright.chromium.launch(
            executable_path=str(CHROMIUM_EXECUTABLE) if CHROMIUM_EXECUTABLE else None,
            headless=config.CHROMIUM_HEADLESS,
        )
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            context.route(
                "**/*",
                lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_(),
            )
            if CHROMIUM_COOKIES_FILE.exists():
                try:
                    cookies = json.loads(CHROMIUM_COOKIES_FILE.read_text(encoding="utf-8"))
                    if isinstance(cookies, list):
                        clean_cookies = []
                        for c in cookies:
                            if not isinstance(c, dict):
                                continue
                            cookie_dict = {
                                "name": str(c.get("name", "")),
                                "value": str(c.get("value", "")),
                                "domain": str(c.get("domain", ".instagram.com")),
                                "path": str(c.get("path", "/")),
                            }
                            if "expires" in c and isinstance(c["expires"], (int, float)) and c["expires"] > 0:
                                cookie_dict["expires"] = float(c["expires"])
                            if "httpOnly" in c:
                                cookie_dict["httpOnly"] = bool(c["httpOnly"])
                            if "secure" in c:
                                cookie_dict["secure"] = bool(c["secure"])
                            if "sameSite" in c:
                                ss = str(c["sameSite"]).capitalize()
                                if ss in {"Strict", "Lax", "None"}:
                                    cookie_dict["sameSite"] = ss
                            if cookie_dict["name"] and cookie_dict["value"]:
                                clean_cookies.append(cookie_dict)
                        if clean_cookies:
                            context.add_cookies(clean_cookies)
                except Exception as err:
                    LOGGER.warning("Could not load cookies for ChromeGroupRemover: %s", err)
            return browser, context
        except Exception:
            try:
                browser.close()
            except Exception:
                pass
            raise

    @staticmethod
    def _click_first_visible(locators: list[object], timeout: int = 2_000) -> bool:
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            for locator in locators:
                try:
                    for index in range(locator.count()):
                        candidate = locator.nth(index)
                        if candidate.is_visible():
                            try:
                                candidate.evaluate("(element) => element.click()")
                            except (PlaywrightError, PlaywrightTimeoutError):
                                candidate.click(timeout=min(timeout, 1_000), force=True)
                            return True
                except (PlaywrightError, PlaywrightTimeoutError):
                    continue
            time.sleep(0.1)
        return False

    def add(self, thread_id: int | str, username: str) -> tuple[bool, str]:
        username = username.lstrip("@").strip()
        if not username or not str(thread_id).isdigit():
            return False, "Chrome add received an invalid account or group."

        browser = None
        context = None
        with self.lock:
            with sync_playwright() as playwright:
                try:
                    browser, context = self._open_context(playwright)
                    page = context.new_page()
                    page.goto(
                        f"https://www.instagram.com/direct/t/{thread_id}/",
                        wait_until="domcontentloaded", timeout=45_000,
                    )
                    if self._is_login_or_challenge(page.url):
                        return False, "Saved Chrome login expired; automatic recovery is required."
                    icon = page.locator('svg[aria-label="Conversation information"]').first
                    icon.wait_for(state="visible", timeout=15_000)
                    info_control = icon.locator("xpath=ancestor::*[@role='button'][1]")
                    if not self._click_first_visible([info_control], timeout=4_000):
                        return False, "Chrome could not open Instagram group details."
                    page.get_by_text(re.compile(r"^members$", re.I), exact=True).first.wait_for(
                        state="visible", timeout=8_000
                    )
                    add_people = page.get_by_text(re.compile(r"^add people$", re.I), exact=True)
                    if not self._click_first_visible([add_people], timeout=4_000):
                        return False, "Chrome could not open Instagram's Add people dialog."
                    dialog = page.get_by_role("dialog").last
                    dialog.wait_for(state="visible", timeout=5_000)
                    search = dialog.locator('input[placeholder="Search..."]').first
                    search.wait_for(state="visible", timeout=5_000)
                    search.fill(username)
                    result = dialog.get_by_text(re.compile(rf"^@?{re.escape(username)}$", re.I)).first
                    result.wait_for(state="visible", timeout=12_000)
                    option = result.locator("xpath=ancestor::*[@role='option'][1]")
                    if not self._click_first_visible([option], timeout=3_000):
                        return False, "Chrome found the account but could not select it."
                    next_button = dialog.get_by_role("button", name=re.compile(r"^next$", re.I))
                    if not self._click_first_visible([next_button], timeout=4_000):
                        return False, "Chrome selected the account but could not click Next."
                    self._click_first_visible([
                        page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^add$", re.I))
                    ], timeout=400)
                    member = page.get_by_text(re.compile(rf"^@?{re.escape(username)}$", re.I)).first
                    member.wait_for(state="visible", timeout=15_000)
                    LOGGER.info("Chrome completed and verified a group-member add")
                    return True, f"Chrome added @{username} to the group."
                except PlaywrightTimeoutError:
                    return False, "Chrome timed out while searching for or adding that account."
                except PlaywrightError as error:
                    LOGGER.warning("Chrome group add failed: %s", error)
                    return False, "Chrome could not complete Instagram's Add people flow."
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            pass

    def remove(self, thread_id: int | str, username: str) -> tuple[bool, str]:
        username = username.lstrip("@").strip()
        if not username or not str(thread_id).isdigit():
            return False, "Chrome removal received an invalid member or group."

        browser = None
        context = None
        with self.lock:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                try:
                    browser, context = self._open_context(playwright)
                    page = context.new_page()
                    page.goto(
                        f"https://www.instagram.com/direct/t/{thread_id}/",
                        wait_until="domcontentloaded",
                        timeout=45_000,
                    )
                    if self._is_login_or_challenge(page.url):
                        return False, "Saved Chrome login expired; automatic recovery is required."

                    info_names = re.compile(r"conversation information|chat information|details|info", re.I)
                    info_icon = page.locator('svg[aria-label="Conversation information"]').first
                    try:
                        info_icon.wait_for(state="visible", timeout=15_000)
                        info_clicked = self._click_first_visible([
                            info_icon.locator("xpath=ancestor::*[@role='button'][1]")
                        ], timeout=4_000)
                    except (PlaywrightError, PlaywrightTimeoutError):
                        info_clicked = self._click_first_visible([
                            page.locator('svg[aria-label="Details"]').locator(
                                "xpath=ancestor::*[@role='button'][1]"
                            ),
                            page.get_by_role("button", name=info_names),
                        ], timeout=4_000)
                    if not info_clicked:
                        return False, "Chrome could not open Instagram group details."
                    try:
                        page.get_by_text(re.compile(r"^members$", re.I), exact=True).first.wait_for(
                            state="visible", timeout=8_000
                        )
                    except (PlaywrightError, PlaywrightTimeoutError):
                        return False, "Chrome opened group details but the member list did not load."

                    exact_member = re.compile(rf"^@?{re.escape(username)}$", re.I)
                    member_locators = [
                        page.locator(f'a[href="/{username}/"], a[href*="/{username}/"], a[href="/{username}"]'),
                        page.get_by_text(exact_member),
                        page.get_by_text(re.compile(rf"\b{re.escape(username)}\b", re.I)),
                        page.locator(f'text="{username}"'),
                    ]
                    member = None
                    for m_loc in member_locators:
                        try:
                            if m_loc.count() > 0 and m_loc.first.is_visible(timeout=1_500):
                                member = m_loc.first
                                break
                        except (PlaywrightError, PlaywrightTimeoutError):
                            continue
                    if member is None:
                        member = page.get_by_text(exact_member).first
                        member.wait_for(state="visible", timeout=12_000)

                    menu_clicked = False
                    option_name = re.compile(r"more|options|menu|\.\.\.", re.I)
                    for level in range(1, 8):
                        row = member.locator(f"xpath=ancestor::div[{level}]")
                        try:
                            if len(row.inner_text(timeout=1_000)) > 800:
                                continue
                            named = row.get_by_role("button", name=option_name)
                            if named.count() and named.first.is_visible(timeout=500):
                                named.first.click(timeout=2_000)
                                menu_clicked = True
                                break
                            opt_svg = row.locator('svg[aria-label*="option" i], svg[aria-label*="more" i], svg[aria-label*="menu" i]')
                            if opt_svg.count() and opt_svg.first.is_visible(timeout=500):
                                opt_svg.first.locator("xpath=ancestor::*[@role='button' or self::button][1]").click(timeout=2_000)
                                menu_clicked = True
                                break
                            buttons = row.locator('[role="button"], button')
                            count = buttons.count()
                            if 0 < count <= 5 and self._click_first_visible([buttons], timeout=2_000):
                                menu_clicked = True
                                break
                        except (PlaywrightError, PlaywrightTimeoutError):
                            continue
                    if not menu_clicked:
                        return False, f"Chrome found @{username} but not its member options button."

                    remove_name = re.compile(r"remove|kick|boot", re.I)
                    if not self._click_first_visible([
                        page.get_by_role("button", name=remove_name),
                        page.get_by_role("menuitem", name=remove_name),
                        page.get_by_text(remove_name),
                        page.locator('[role="menu"] [role="menuitem"]:has-text("Remove")'),
                        page.locator('button:has-text("Remove")'),
                    ], timeout=5_000):
                        return False, "Chrome could not find Instagram's Remove action."

                    confirmation = page.get_by_role("dialog").get_by_role(
                        "button", name=re.compile(r"^remove$", re.I)
                    )
                    self._click_first_visible([
                        confirmation,
                        page.locator('[role="dialog"] button:has-text("Remove")'),
                    ], timeout=1_000)
                    try:
                        member.wait_for(state="hidden", timeout=8_000)
                    except (PlaywrightError, PlaywrightTimeoutError):
                        return False, "Chrome clicked Remove, but Instagram still shows the member in the group."

                    LOGGER.info("Chrome completed and verified a group-member removal")
                    return True, f"Chrome removed @{username} from the group."
                except PlaywrightTimeoutError:
                    return False, "Chrome timed out while opening the group or member list."
                except PlaywrightError as error:
                    LOGGER.warning("Chrome group removal failed: %s", error)
                    return False, "Chrome could not complete Instagram's Remove flow."
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            pass
