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

        def log_request(request):
            if "api" in request.url.lower() or "search" in request.url.lower() or "flight" in request.url.lower():
                print(f"REQ: {request.method} {request.url}")

        def log_response(response):
            try:
                if "application/json" in response.headers.get("content-type", ""):
                    url = response.url.lower()
                    if "api" in url or "search" in url or "flight" in url:
                        print(f"RES: {response.status} {response.url}")
                        # print(f"DATA: {response.json()}")
            except Exception:
                pass

        page.on("request", log_request)
        page.on("response", log_response)

        print("Navigating to SpiceJet...")
        try:
            page.goto("https://www.spicejet.com/", wait_until="domcontentloaded", timeout=45000)
            print("Page loaded. Now trying to perform a search...")
            page.wait_for_timeout(5000)
            
            # Close any popups
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

            # We'll just dump the HTML so I can see the structure of the homepage
            # to know how to interact with origin/destination/date/search button
            html = page.content()
            with open("spicejet_home.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("Saved spicejet_home.html")
            
        except Exception as e:
            print(f"Error: {e}")

        browser.close()

if __name__ == "__main__":
    main()
