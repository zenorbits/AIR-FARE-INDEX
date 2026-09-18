import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from scraper.akasa import _payload_matches, _ordinal_suffix, select_airport, AkasaScraper
from main import is_route_supported

def test_payload_matches_expected_date():
    payload = {
        "data": {
            "results": [
                {
                    "trips": [
                        {"date": "2023-10-01T10:00:00"}
                    ]
                }
            ]
        }
    }
    assert _payload_matches(payload, "2023-10-01") is True

def test_payload_matches_different_date():
    payload = {
        "data": {
            "results": [
                {
                    "trips": [
                        {"date": "2023-10-05T10:00:00"}
                    ]
                }
            ]
        }
    }
    assert _payload_matches(payload, "2023-10-01") is False

def test_payload_matches_malformed():
    payload = {"data": {"results": [{"trips": [{"wrong_key": "val"}]}]}}
    assert _payload_matches(payload, "2023-10-01") is False
    
    payload_no_data = {}
    assert _payload_matches(payload_no_data, "2023-10-01") is False

@patch("scraper.akasa.sync_playwright")
@patch("scraper.akasa.parse_akasa_response")
def test_scrape_flow(mock_parse, mock_sync_playwright):
    # Mock patchright context
    mock_p = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    
    mock_sync_playwright.return_value.__enter__.return_value = mock_p
    mock_p.chromium.launch_persistent_context.return_value = mock_context
    mock_context.pages = [mock_page]
    
    # Mock handle_response to simulate interception
    def trigger_response(*args, **kwargs):
        handler = mock_page.on.call_args[0][1]
        
        # Create fake response
        fake_response = MagicMock()
        fake_response.url = "https://api.akasaair.com/api/v1/availability/search"
        fake_response.request.method = "POST"
        fake_response.status = 200
        fake_response.json.return_value = {
            "data": {
                "results": [{"trips": [{"date": "2026-10-01"}]}]
            }
        }
        handler(fake_response)
    
    mock_page.goto.side_effect = trigger_response
    mock_parse.return_value = [{"flight": "QP-123"}]

    # select_airport() checks input_value 3 times: before clear, after clear, after type
    mock_page.locator.return_value.first.input_value.side_effect = [
        "Delhi (DEL)", "", "Delhi (DEL)",
        "Mumbai (BOM)", "", "Mumbai (BOM)",
        "Delhi (DEL)", "", "Delhi (DEL)"
    ]

    scraper = AkasaScraper()
    results = scraper.scrape("DEL", "BOM", "01/10/2026", 7)

    # Verify the flow
    assert len(results) == 1
    assert results[0]["flight"] == "QP-123"

    # Verify selectors were called
    mock_page.locator.assert_any_call("input#oneway")
    mock_page.locator.assert_any_call("input#From")
    mock_page.locator.assert_any_call("input#To")
    mock_page.locator.assert_any_call("#destinations li")
    mock_page.locator.assert_any_call("input[name='DepartureDate']")
    mock_page.locator.assert_any_call("button:has-text('Search Flights')")


def test_select_airport_selects_matching_option():
    mock_page = MagicMock()
    # First call: not empty, Second call: empty after clearing, Third call: contains DEL after typing
    mock_page.locator.return_value.first.input_value.side_effect = ["Delhi (DEL)", "", "Delhi (DEL)", "Delhi (DEL)"]

    select_airport(mock_page, "input#From", "DEL")

    mock_page.locator.assert_any_call("input#From")
    mock_page.locator.assert_any_call("#destinations li")
    field = mock_page.locator.return_value.first
    field.click.assert_called_once()
    field.press_sequentially.assert_called_once_with("DEL", delay=150)
    option = mock_page.locator.return_value.filter.return_value.first
    option.wait_for.assert_called_once_with(state="visible", timeout=10000)
    option.click.assert_called_once()


def test_select_airport_retries_then_raises():
    mock_page = MagicMock()
    # Field value never contains the code, so verification keeps failing.
    mock_page.locator.return_value.first.input_value.return_value = ""

    with pytest.raises(RuntimeError):
        select_airport(mock_page, "input#From", "DEL")

    field = mock_page.locator.return_value.first
    assert field.click.call_count == 2  # initial attempt + 1 retry

def test_ordinal_suffix():
    assert _ordinal_suffix(1) == "st"
    assert _ordinal_suffix(2) == "nd"
    assert _ordinal_suffix(3) == "rd"
    assert _ordinal_suffix(4) == "th"
    assert _ordinal_suffix(11) == "th"
    assert _ordinal_suffix(21) == "st"
    assert _ordinal_suffix(22) == "nd"
    assert _ordinal_suffix(31) == "st"

def test_is_route_supported():
    class ScraperWithSupport:
        SUPPORTED_ROUTES = {("DEL", "BOM"), ("BOM", "BLR")}
        
    class ScraperWithoutSupport:
        pass
        
    scraper_with = ScraperWithSupport()
    scraper_without = ScraperWithoutSupport()
    
    assert is_route_supported(scraper_with, "DEL", "BOM") is True
    assert is_route_supported(scraper_with, "DEL", "CCU") is False
    assert is_route_supported(scraper_without, "DEL", "CCU") is True
