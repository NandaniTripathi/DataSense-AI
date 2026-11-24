"""Optional end-to-end browser test (not run by pytest).

Start the backend with the built frontend and the mock LLM, then run:
    python tests/e2e_browser.py

Needs `pip install playwright` and a Chromium install.
"""
import os
import re

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
SHOTS = os.environ.get("SHOTS_DIR", "/tmp/shots")
os.makedirs(SHOTS, exist_ok=True)
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errors.append(("console", m.text)) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))

    pg.goto(BASE + "/")
    pg.wait_for_url(re.compile(r".*/datasets"), timeout=8000)
    print("redirected to", pg.url)
    pg.screenshot(path=f"{SHOTS}/1_datasets_empty.png")

    pg.get_by_role("button", name="Try the demo dataset").click()
    pg.wait_for_url(re.compile(r".*/dashboard"), timeout=20000)
    pg.get_by_text("Anomalies", exact=True).wait_for(timeout=20000)
    pg.wait_for_selector(".js-plotly-plot", timeout=20000)
    pg.wait_for_timeout(1500)
    print("plotly charts on dashboard:", pg.locator(".js-plotly-plot").count())
    pg.screenshot(path=f"{SHOTS}/2_dashboard.png", full_page=True)

    pg.get_by_role("button", name="Generate with AI").click()
    pg.get_by_text("Mock summary bullet").wait_for(timeout=8000)
    print("AI summary rendered")

    pg.get_by_role("link", name="Explorer").click()
    pg.get_by_text("Page 1 of").wait_for()
    pg.get_by_placeholder("Search any column...").fill("Furniture")
    pg.wait_for_timeout(900)
    print("explorer:", pg.get_by_text(re.compile(r"matching rows")).inner_text())
    pg.get_by_role("button", name="revenue").click()
    pg.wait_for_timeout(600)
    pg.screenshot(path=f"{SHOTS}/3_explorer.png")
    pg.get_by_role("tab", name=re.compile("Columns")).click()
    pg.wait_for_timeout(300)
    pg.screenshot(path=f"{SHOTS}/3b_columns.png", full_page=True)

    pg.get_by_role("link", name="AI Analyst").click()
    pg.get_by_text("Ask anything about").wait_for()
    pg.screenshot(path=f"{SHOTS}/4_analyst_empty.png")
    box = pg.get_by_placeholder("Ask a question about your data...")
    box.fill("total revenue by region")
    box.press("Enter")
    pg.get_by_text("Mock answer for: total revenue by region").wait_for(timeout=15000)
    pg.wait_for_selector(".js-plotly-plot", timeout=15000)
    pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{SHOTS}/5_analyst_answer.png")
    pg.get_by_role("tab", name="SQL").click()
    print("SQL shown:", pg.locator("pre").first.inner_text()[:60])
    pg.get_by_role("tab", name=re.compile("Table")).click()
    pg.get_by_role("button", name="What about by month?").click()
    pg.get_by_text("5 messages").wait_for(timeout=15000)
    print("follow-up chip works; history sent")
    with pg.expect_download() as dl:
        pg.get_by_role("link", name="CSV").first.click()
    print("csv download:", dl.value.suggested_filename)

    pg.get_by_role("link", name="Reports").click()
    pg.get_by_text("analytical report").wait_for()
    with pg.expect_download() as dl:
        pg.get_by_role("link", name="Download report").click()
    print("report download:", dl.value.suggested_filename)
    pg.screenshot(path=f"{SHOTS}/6_reports.png")

    m = b.new_page(viewport={"width": 390, "height": 844})
    m.on("pageerror", lambda e: errors.append(("pageerror-mobile", str(e))))
    m.goto(BASE + "/dashboard")
    m.get_by_text("Anomalies", exact=True).wait_for(timeout=20000)
    m.wait_for_timeout(1500)
    m.screenshot(path=f"{SHOTS}/7_mobile_dashboard.png")
    print("mobile horizontal overflow:", m.evaluate("document.documentElement.scrollWidth > window.innerWidth"))
    m.get_by_label("Open menu").click()
    m.wait_for_timeout(300)
    m.screenshot(path=f"{SHOTS}/8_mobile_menu.png")
    b.close()

print("ERRORS:", errors)
