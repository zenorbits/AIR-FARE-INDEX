import os
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("Navigating...")
        url = "https://flight.easemytrip.com/FlightList/Index?srch=DEL-BOM-15/10/2026-1-0-0-E-100"
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(5000)
            page.screenshot(path="screenshot.png")
            print("Saved screenshot.png")
        except Exception as e:
            print(f"Error: {e}")

        browser.close()

if __name__ == "__main__":
    main()
