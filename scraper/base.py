from abc import ABC, abstractmethod
from typing import List, Dict, Any
import random
import time
import logging

logger = logging.getLogger(__name__)

class BaseScraper(ABC):
    """
    Abstract base class for all flight scrapers.
    """
    
    @abstractmethod
    def scrape(self, origin: str, destination: str, travel_date: str, lead_time_days: int) -> List[Dict[str, Any]]:
        """
        Scrapes flight data for a given route and date.
        
        :param origin: 3-letter IATA code for origin airport.
        :param destination: 3-letter IATA code for destination airport.
        :param travel_date: String representation of the travel date (e.g., 'DD/MM/YYYY').
        :param lead_time_days: Number of days between scrape date and travel date.
        :return: A list of dictionaries representing individual flight results.
        """
        pass
    
    def random_delay(self, min_seconds: float = 30.0, max_seconds: float = 90.0):
        """
        Pauses execution for a random duration to avoid rate limiting.
        """
        delay = random.uniform(min_seconds, max_seconds)
        logger.info(f"Sleeping for {delay:.2f} seconds to avoid rate limits...")
        time.sleep(delay)
