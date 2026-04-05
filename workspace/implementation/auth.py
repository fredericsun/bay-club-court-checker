# workspace/implementation/auth.py
import logging
import os
import sys
from typing import Optional

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

MAX_LOGIN_RETRIES = 3
PORTAL_LOGIN_URL = "https://bayclubconnect.com/account/login/connect"


class LoginError(Exception):
    """Raised when portal login fails after all retry attempts."""


def load_credentials() -> tuple[str, str]:
    """Load BAY_CLUB_USERNAME and BAY_CLUB_PASSWORD from environment."""
    username = os.environ.get("BAY_CLUB_USERNAME")
    if not username:
        print("Error: BAY_CLUB_USERNAME not set")
        sys.exit(1)
    password = os.environ.get("BAY_CLUB_PASSWORD")
    if not password:
        print("Error: BAY_CLUB_PASSWORD not set")
        sys.exit(1)
    return username, password


async def login_and_get_token(username: str, password: str) -> str:
    """Log in via Playwright and return the bearer access token."""
    token: Optional[str] = None
    last_exc: Optional[Exception] = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def on_response(response):
            nonlocal token
            if "authentication2-api.bayclubs.io/connect/token" in response.url:
                try:
                    body = await response.json()
                    if "access_token" in body:
                        token = body["access_token"]
                except Exception:
                    pass

        page.on("response", on_response)

        for attempt in range(1, MAX_LOGIN_RETRIES + 1):
            logger.debug("Login attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
            try:
                await page.goto(PORTAL_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector('input[placeholder="Member ID or Username"]', timeout=15_000)
                await page.fill('input[placeholder="Member ID or Username"]', username)
                await page.fill('input[placeholder="Password"]', password)
                await page.click('button:has-text("LOG IN")')
                await page.wait_for_url(lambda url: "login" not in url, timeout=30_000)
                logger.info("Login succeeded on attempt %d of %d", attempt, MAX_LOGIN_RETRIES)
                break
            except PlaywrightTimeoutError as exc:
                logger.warning("Login attempt %d/%d timed out, retrying", attempt, MAX_LOGIN_RETRIES)
                last_exc = exc
                if attempt == MAX_LOGIN_RETRIES:
                    await browser.close()
                    raise LoginError(f"Login failed after {MAX_LOGIN_RETRIES} attempts.") from last_exc

        await browser.close()

    if not token:
        raise LoginError("Login succeeded but auth token was not captured.")

    return token
