import os
import json
import urllib.parse
from playwright.sync_api import sync_playwright

def main():
    origin = "DEL"
    destination = "BOM"
    date = "15/10/2026"
    date_encoded = urllib.parse.quote(date, safe="")
    
    url = f"https://flight.yatra.com/air-search-ui/dom2/trigger?flex=0&viewName=normal&source=fresco-flights&type=O&class=Economy&ADT=1&CHD=0&INF=0&noOfSegments=1&origin={origin}&originCountry=IN&destination={destination}&destinationCountry=IN&flight_depart_date={date_encoded}"

    with sync_playwright() as p:
        # Use persistent context to build cookies/history and pass anti-bot checks
        context = p.chromium.launch_persistent_context(
            user_data_dir="./yatra_browser_profile",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation={"longitude": 77.2090, "latitude": 28.6139},
            permissions=["geolocation"]
        )
        
        page = context.pages[0] if context.pages else context.new_page()
        
        # Load stealth AFTER we have the page
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        
        captured_data = []
        
        # Event handler for responses
        def handle_response(response):
            if "application/json" in response.headers.get("content-type", ""):
                try:
                    url = response.url
                    # Skip common telemetry/tracking URLs to reduce noise
                    if "analytics" in url or "tracking" in url or "events" in url:
                        return
                    
                    data = response.json()
                    captured_data.append({
                        "url": url,
                        "status": response.status,
                        "data": data
                    })
                except Exception as e:
                    pass

        page.on("response", handle_response)
        
        print(f"Navigating to: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print("Page loaded, waiting 20s to ensure API response is captured...")
            page.wait_for_timeout(20000)
        except Exception as e:
            print(f"Error navigating: {e}")
            page.screenshot(path="yatra_error.png")
            
        with open("yatra_api_response.json", "w") as f:
            json.dump(captured_data, f, indent=2)
        print(f"Successfully saved {len(captured_data)} JSON responses to yatra_api_response.json")
            
        context.close()

if __name__ == "__main__":
    main()
