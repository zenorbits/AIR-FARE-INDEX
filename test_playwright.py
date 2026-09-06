import os
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def log_request(request):
            if "easemytrip.com" in request.url and ("api" in request.url.lower() or "flight" in request.url.lower() or "json" in request.url.lower()):
                print(f"Request: {request.url}")

        def log_response(response):
            try:
                if "easemytrip.com" in response.url and "application/json" in response.headers.get("content-type", ""):
                    print(f"Response (JSON): {response.url}")
            except Exception as e:
                pass

        page.on("request", log_request)
        page.on("response", log_response)

        print("Navigating...")
        # Construct a flight search URL (Delhi to Mumbai for a future date)
        url = "https://flight.easemytrip.com/FlightList/Index?srch=DEL-BOM-15/10/2026-1-0-0-E-100"
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            print("Navigation complete.")
        except Exception as e:
            print(f"Navigation error: {e}")

        page.wait_for_timeout(5000)
        browser.close()

if __name__ == "__main__":
    main()
