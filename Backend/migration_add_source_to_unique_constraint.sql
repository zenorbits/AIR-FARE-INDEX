ALTER TABLE flight_prices DROP CONSTRAINT uq_flight_departure_scraped_hour;
ALTER TABLE flight_prices ADD CONSTRAINT uq_source_flight_departure_scraped_hour UNIQUE (source, flight_number, departure_time, scraped_hour);
