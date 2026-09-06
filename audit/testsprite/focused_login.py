import asyncio
from playwright import async_api
from playwright.async_api import expect


async def run_test():
    async with async_api.async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--window-size=1280,720", "--disable-dev-shm-usage", "--ipc=host"],
        )
        context = await browser.new_context()
        context.set_default_timeout(15000)
        page = await context.new_page()
        responses = []

        async def capture(response):
            if response.url.endswith("/api/auth/login"):
                responses.append({
                    "url": response.url,
                    "method": response.request.method,
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                    "body": (await response.text())[:300],
                })

        page.on("response", capture)
        try:
            await page.goto("VAR_{url}/login")
            await page.get_by_test_id("login-email").fill("ff-audit-invalid@example.com")
            await page.get_by_test_id("login-password").fill("invalid-audit-password")
            await page.get_by_test_id("login-submit").click()
            await expect(page.get_by_test_id("login-error")).to_contain_text(
                "Invalid email or password", timeout=15000
            )
            await expect(page).to_have_url("/login", timeout=5000)
            assert len(responses) == 1, responses
            result = responses[0]
            assert result["method"] == "POST", result
            assert result["status"] == 401, result
            assert result["content_type"].startswith("application/json"), result
            assert result["body"] == '{"detail":"Invalid email or password"}', result
            assert "password" not in result["body"].lower(), result
        finally:
            await browser.close()


asyncio.run(run_test())
