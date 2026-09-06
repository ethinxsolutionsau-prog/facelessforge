"""Read-only HTTP assertions in an actual browser; no DOM-only false passes."""
import asyncio
import json
from playwright.async_api import async_playwright

async def run_test():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto('https://facelessforge.ethinx.solutions/login')
            failures = []
            for path in ['/api/users/me', '/api/projects', '/api/assets', '/api/analytics']:
                data = await page.evaluate('''async path => {
                    const r = await fetch(path, {credentials: 'omit'});
                    const text = await r.text();
                    let detail = null;
                    try { detail = JSON.parse(text).detail; } catch (_) {}
                    return {url: new URL(path, location.origin).href, method: 'GET',
                      status: r.status, contentType: r.headers.get('content-type'),
                      html: /^\\s*<!doctype html|^\\s*<html/i.test(text),
                      detail: typeof detail === 'string' ? detail.slice(0, 120) : null};
                }''', path)
                print(json.dumps(data))
                allowed = [401, 403] if path == '/api/users/me' else [401, 403, 404]
                if data['status'] not in allowed or 'application/json' not in (data['contentType'] or '') or data['html']:
                    failures.append(path)
            assert not failures, 'API authorization/JSON contract failed: ' + ', '.join(failures)
        finally:
            await browser.close()

asyncio.run(run_test())
