"""
Fetch the current Ocp-Apim-Subscription-Key from the Bay Club web app.

Run this if the checker starts returning 401 errors:
    python get_api_key.py

Then update API_SUBSCRIPTION_KEY in checker.py with the new value.
"""

import asyncio
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


async def main():
    key = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        def on_request(request):
            nonlocal key
            if "connect-api.bayclubs.io" in request.url:
                k = request.headers.get("ocp-apim-subscription-key")
                if k:
                    key = k

        page.on("request", on_request)

        await page.goto("https://bayclubconnect.com/account/login/connect", wait_until="networkidle", timeout=30000)
        await page.fill('input[placeholder="Member ID or Username"]', os.environ["BAY_CLUB_USERNAME"])
        await page.fill('input[placeholder="Password"]', os.environ["BAY_CLUB_PASSWORD"])
        await page.click('button:has-text("LOG IN")')
        await page.wait_for_url(lambda url: "login" not in url, timeout=15000)
        await page.goto("https://bayclubconnect.com/racquet-sports/create-booking", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        await browser.close()

    if key:
        print(f"API key: {key}")
        print(f'\nUpdate checker.py line:\nAPI_SUBSCRIPTION_KEY = "{key}"')
    else:
        print("Could not capture key. Try running again.")


if __name__ == "__main__":
    asyncio.run(main())
