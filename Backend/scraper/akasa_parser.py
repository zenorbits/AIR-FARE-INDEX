import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def parse_akasa_response(payload: dict, origin: str, destination: str, lead_time: int, scraped_at: datetime) -> list:
    """
    Parses the Akasa Air API response payload offline.
    """
    results = []
    scraped_hour = scraped_at.replace(minute=0, second=0, microsecond=0)
    
    try:
        # Create fare lookup dictionary
        fares_available = payload.get("data", {}).get("faresAvailable", [])
        fare_lookup = {item["key"]: item["value"] for item in fares_available}
        
        # Extract journeys
        flight_results = payload.get("data", {}).get("results", [])
        
        for result in flight_results:
            trips = result.get("trips", [])
            for trip in trips:
                journeys_by_market = trip.get("journeysAvailableByMarket", [])
                for market in journeys_by_market:
                    journeys = market.get("value", [])
                    
                    for journey in journeys:
                        designator = journey.get("designator", {})
                        j_origin = designator.get("origin")
                        j_dest = designator.get("destination")
                        flight_type = journey.get("flightType")
                        segments = journey.get("segments", [])
                        
                        # Filter criteria
                        if j_origin != origin or j_dest != destination:
                            continue
                        if flight_type != "NonStop":
                            continue
                        if len(segments) != 1:
                            continue
                            
                        try:
                            # Assuming designator.departure is ISO format e.g., "2023-12-01T10:00:00"
                            dep_time = datetime.fromisoformat(designator.get("departure", ""))
                        except (ValueError, TypeError):
                            logger.warning(f"Failed to parse departure time for {j_origin}-{j_dest}")
                            continue

                        # Find the lowest fare
                        lowest_total = float('inf')
                        best_fare_data = None
                        
                        journey_fares = journey.get("fares", [])
                        for fare_ref in journey_fares:
                            fare_key = fare_ref.get("fareAvailabilityKey")
                            if fare_key not in fare_lookup:
                                logger.warning(f"Fare key missing from lookup: {fare_key}")
                                continue
                                
                            try:
                                fare_details = fare_lookup[fare_key]["fares"][0]["passengerFares"][0]
                                total = float(fare_details.get("fareAmount", 0))
                                
                                if total < lowest_total:
                                    lowest_total = total
                                    
                                    service_charges = fare_details.get("serviceCharges", [])
                                    base = sum(float(c.get("amount", 0)) for c in service_charges if c.get("type") == "FarePrice")
                                    taxes = total - base
                                    
                                    best_fare_data = {
                                        "total": total,
                                        "base": base,
                                        "taxes": taxes,
                                        "service_charges": service_charges
                                    }
                            except (KeyError, IndexError, ValueError) as e:
                                logger.warning(f"Error parsing fare details for key {fare_key}: {e}")
                                continue
                        
                        if best_fare_data:
                            flight_identifier = segments[0].get("identifier", {}).get("identifier", "UNKNOWN")
                            flight_number = f"QP-{flight_identifier}"
                            
                            # Log debug information
                            logger.debug(f"DEBUG {flight_number}: total={best_fare_data['total']}, base={best_fare_data['base']}")
                            for charge in best_fare_data['service_charges']:
                                logger.debug(f"  Charge: type={charge.get('type')}, code={charge.get('code')}, amount={charge.get('amount')}")
                            
                            results.append({
                                "source": "akasa",
                                "route": f"{origin}-{destination}",
                                "airline": "QP",
                                "flight_number": flight_number,
                                "cabin_class": "ECONOMY",
                                "base_fare": best_fare_data["base"],
                                "taxes_fees": best_fare_data["taxes"],
                                "total_fare": best_fare_data["total"],
                                "stops": 0,
                                "lead_time_days": lead_time,
                                "departure_time": dep_time,
                                "scraped_at": scraped_at,
                                "scraped_hour": scraped_hour
                            })
                            
    except Exception:
        logger.exception("Failed to parse Akasa payload")
        return []
        
    if not results:
        logger.info(f"No non-stop flights found for {origin}-{destination}.")
        
    return results
