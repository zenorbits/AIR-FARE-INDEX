import os
import json
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        api_responses = []

        def log_response(response):
            try:
                if "application/json" in response.headers.get("content-type", ""):
                    if "search" in response.url.lower() or "flight" in response.url.lower():
                        if response.status == 200:
                            data = response.json()
                            api_responses.append((response.url, data))
            except Exception:
                pass

        page.on("response", log_response)

        print("Navigating to SpiceJet...")
        page.goto("https://www.spicejet.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        print("Forcing form fill with JS...")
        try:
            # Force click the origin
            page.evaluate('''(function() {
                let originDiv = document.querySelector('[data-testid="to-testID-origin"]');
                if (originDiv) {
                    let input = originDiv.querySelector('input');
                    if (input) {
                        input.value = "DEL";
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                    } else {
                        originDiv.click();
                    }
                }
            })()''')
            page.wait_for_timeout(1000)
            page.keyboard.type("DEL", delay=100)
            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")

            page.evaluate('''(function() {
                let destDiv = document.querySelector('[data-testid="to-testID-destination"]');
                if (destDiv) {
                    destDiv.click();
                }
            })()''')
            page.wait_for_timeout(1000)
            page.keyboard.type("BOM", delay=100)
            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")
            
            # Click search
            page.evaluate('''(function() {
                let btn = document.querySelector('[data-testid="test-id-search-btn"]');
                if (btn) btn.click();
            })()''')
            
            print("Waiting for search results...")
            page.wait_for_timeout(15000)

            # Dump API responses
            with open("spicejet_api.json", "w", encoding="utf-8") as f:
                json.dump(api_responses, f, indent=2)
            print("Saved spicejet_api.json")

            html = page.content()
            with open("spicejet_search.html", "w", encoding="utf-8") as f:
                f.write(html)
            
        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="spicejet_error2.png")

        browser.close()

if __name__ == "__main__":
    main()
