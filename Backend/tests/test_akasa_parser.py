from datetime import datetime
from scraper.akasa_parser import parse_akasa_response

def test_parse_akasa_filters_incorrect_origin():
    # DXN|BOM journey is filtered out when origin="DEL"
    payload = {
        "data": {
            "faresAvailable": [],
            "results": [{"trips": [{"journeysAvailableByMarket": [{"value": [
                {
                    "designator": {"origin": "DXN", "destination": "BOM", "departure": "2023-10-01T10:00:00"},
                    "flightType": "NonStop",
                    "segments": [{"identifier": {"identifier": "123"}}],
                    "fares": []
                }
            ]}]}]}]
        }
    }
    results = parse_akasa_response(payload, "DEL", "BOM", 7, datetime.now())
    assert len(results) == 0

def test_parse_akasa_filters_multi_segment():
    # multi-segment journey is filtered out
    payload = {
        "data": {
            "faresAvailable": [],
            "results": [{"trips": [{"journeysAvailableByMarket": [{"value": [
                {
                    "designator": {"origin": "DEL", "destination": "BOM", "departure": "2023-10-01T10:00:00"},
                    "flightType": "NonStop",
                    "segments": [{"identifier": {"identifier": "123"}}, {"identifier": {"identifier": "124"}}],
                    "fares": []
                }
            ]}]}]}]
        }
    }
    results = parse_akasa_response(payload, "DEL", "BOM", 7, datetime.now())
    assert len(results) == 0

def test_parse_akasa_lowest_fare_selection():
    # lowest-total fare is chosen when a journey has 2 fare refs; missing key skipped
    payload = {
        "data": {
            "faresAvailable": [
                {
                    "key": "FARE_EXPENSIVE",
                    "value": {"fares": [{"passengerFares": [{
                        "fareAmount": 8000,
                        "serviceCharges": [{"type": "FarePrice", "amount": 7000}]
                    }]}]}
                },
                {
                    "key": "FARE_CHEAP",
                    "value": {"fares": [{"passengerFares": [{
                        "fareAmount": 5000,
                        "serviceCharges": [{"type": "FarePrice", "amount": 4000}]
                    }]}]}
                }
            ],
            "results": [{"trips": [{"journeysAvailableByMarket": [{"value": [
                {
                    "designator": {"origin": "DEL", "destination": "BOM", "departure": "2023-10-01T10:00:00"},
                    "flightType": "NonStop",
                    "segments": [{"identifier": {"identifier": "123"}}],
                    "fares": [
                        {"fareAvailabilityKey": "FARE_MISSING"},
                        {"fareAvailabilityKey": "FARE_EXPENSIVE"},
                        {"fareAvailabilityKey": "FARE_CHEAP"}
                    ]
                }
            ]}]}]}]
        }
    }
    
    scraped_at = datetime(2023, 9, 24, 15, 30, 0)
    results = parse_akasa_response(payload, "DEL", "BOM", 7, scraped_at)
    
    assert len(results) == 1
    assert results[0]["total_fare"] == 5000
    assert results[0]["base_fare"] == 4000
    assert results[0]["taxes_fees"] == 1000

def test_parse_akasa_base_taxes_split():
    # Base/taxes split: FarePrice 5640 + other charges, fareAmount 6880 -> base 5640, taxes 1240
    payload = {
        "data": {
            "faresAvailable": [
                {
                    "key": "FARE_1",
                    "value": {"fares": [{"passengerFares": [{
                        "fareAmount": 6880,
                        "serviceCharges": [
                            {"type": "FarePrice", "code": "BASE", "amount": 5640},
                            {"type": "Tax", "code": "UDF", "amount": 1000},
                            {"type": "Fee", "code": "CUTE", "amount": 240}
                        ]
                    }]}]}
                }
            ],
            "results": [{"trips": [{"journeysAvailableByMarket": [{"value": [
                {
                    "designator": {"origin": "DEL", "destination": "BOM", "departure": "2023-10-01T10:00:00"},
                    "flightType": "NonStop",
                    "segments": [{"identifier": {"identifier": "1940"}}],
                    "fares": [{"fareAvailabilityKey": "FARE_1"}]
                }
            ]}]}]}]
        }
    }
    
    results = parse_akasa_response(payload, "DEL", "BOM", 7, datetime.now())
    
    assert len(results) == 1
    assert results[0]["flight_number"] == "QP-1940"
    assert results[0]["total_fare"] == 6880
    assert results[0]["base_fare"] == 5640
    assert results[0]["taxes_fees"] == 1240

def test_parse_akasa_empty_market():
    # a market with value [] returns []
    payload = {
        "data": {
            "faresAvailable": [],
            "results": [{"trips": [{"journeysAvailableByMarket": [{"value": []}]}]}]
        }
    }
    results = parse_akasa_response(payload, "DEL", "BOM", 7, datetime.now())
    assert len(results) == 0
