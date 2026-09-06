import asyncio
import re
import uuid

from playwright import async_api
from playwright.async_api import expect


async def run_test():
    email = f"ff-local-{uuid.uuid4().hex}@example.invalid"
    password = "Local-Only-QA-Password-123"
    async with async_api.async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--window-size=1280,720", "--disable-dev-shm-usage", "--ipc=host"],
        )
        context = await browser.new_context()
        context.set_default_timeout(15000)
        page = await context.new_page()
        try:
            base = "VAR_{url}"
            await page.goto(base)
            await page.evaluate("localStorage.setItem('forge_invite', 'FORGE')")
            await page.goto(base + "/register")
            responses = []

            async def capture(response):
                if response.url.endswith("/api/auth/register"):
                    responses.append(response)

            page.on("response", capture)
            await page.get_by_test_id("register-name").fill("FF Local QA")
            await page.get_by_test_id("register-email").fill(email)
            await page.get_by_test_id("register-password").fill(password)
            await page.get_by_test_id("register-submit").click()
            await expect(page).to_have_url(re.compile(r"/app"), timeout=15000)
            assert responses, "registration response was not observed"
            assert responses[-1].status == 200, responses[-1].status
            await expect(page.get_by_test_id("register-error")).not_to_be_visible()
        finally:
            await browser.close()


asyncio.run(run_test())
