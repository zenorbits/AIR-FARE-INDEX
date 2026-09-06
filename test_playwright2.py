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
            if "easemytrip" in request.url and ("api" in request.url.lower() or "flight" in request.url.lower() or "json" in request.url.lower()):
                print(f"Request: {request.url}")

        def log_response(response):
            try:
                if "easemytrip" in response.url and "application/json" in response.headers.get("content-type", ""):
                    print(f"Response (JSON): {response.url}")
            except Exception as e:
                pass

        page.on("request", log_request)
        page.on("response", log_response)

        print("Navigating...")
        # A closer date
        url = "https://flight.easemytrip.com/FlightList/Index?srch=DEL-BOM-15/09/2026-1-0-0-E-100"
        
        try:
            page.goto(url, wait_until="domcontentloaded")
            print("Waiting for flight results...")
            # wait until the loading spinner disappears or flight results appear
            page.wait_for_function("() => !document.body.innerText.includes('Just a moment')", timeout=45000)
            page.wait_for_timeout(2000)
            
            html = page.content()
            with open("page2.html", "w", encoding="utf-8") as f:
                f.write(html)
            page.screenshot(path="screenshot2.png")
            print("Done")
        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="screenshot_error.png")

        browser.close()

if __name__ == "__main__":
    main()
