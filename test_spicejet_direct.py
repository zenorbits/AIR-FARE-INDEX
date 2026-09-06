import os
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        print("Navigating to SpiceJet search URL...")
        # A URL format that might work for SpiceJet React app
        url = "https://www.spicejet.com/search?from=DEL&to=BOM&tripType=1&departure=2026-10-15&adult=1&currency=INR"
        page.goto(url, wait_until="domcontentloaded")
        
        print("Waiting for page to stabilize...")
        page.wait_for_timeout(15000)

        html = page.content()
        with open("spicejet_search_direct.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved spicejet_search_direct.html")

        # try to get elements
        print(page.evaluate('document.querySelectorAll("div").length'))

        browser.close()

if __name__ == "__main__":
    main()
