# Flight Scraper & Airfare Index (APIx)

This is the backend for SIH26056, a real-time Airfare Price Index (APIx) system for MoSPI, scraping domestic Indian flight fares and computing a CPI-style price index.

## Tech Stack
- Python 3.12
- Playwright + playwright-stealth
- SQLAlchemy + psycopg
- PostgreSQL
- FastAPI

## Setup Instructions
1. Clone the repository to your local machine.
2. Create a Python 3.12 virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the example environment file and configure it:
   ```bash
   cp .env.example .env
   ```
   *Make sure to fill in a real `DATABASE_URL` in the `.env` file.*
5. Install the required Playwright browsers (if not already installed):
   ```bash
   playwright install chromium
   ```

## How to Run Each Component

Run the components in the following order:

1. **Scraper**: Reads `config.yaml` for routes and lead-times to scrape raw data.
   ```bash
   python main.py
   ```
2. **Cleaning Pipeline**: Cleans the raw data and inserts it into the `flight_prices_clean` table.
   ```bash
   python -m cleaning.pipeline
   ```
3. **Index Calculator**: Computes the Jevons APIx and stores it in the `airfare_index` table.
   ```bash
   python -m index_calc.jevons
   ```
4. **API**: Starts the FastAPI server (interactive documentation available at `/docs`).
   ```bash
   python -m uvicorn api.main:app --reload
   ```

## Current Data Source
The primary current data source is **Yatra**, utilizing network interception of their internal `get-fare` API.
*Note: EaseMyTrip and SpiceJet were attempted but blocked by robust anti-bot measures. Multi-source scraping is planned as future work.*

## Current Limitations
To state honestly, the system currently has a few limitations:
- **Two live sources (Yatra since Sept 5, Cleartrip since Sept 11)** out of 11 OTAs.
- **Index base day is 2026-09-11**, the first day both sources were live.
- **Nonstop flights only**: Cleartrip's feed is nonstop-only, so Yatra's connecting flights are excluded for comparability.
- **Akasa Air is covered through the OTAs** (no direct scrape).
- **Equal-Weighted Route Index**: The index uses equal weighting across all routes (DGCA traffic-based route weighting is planned but not yet implemented).
- **No Automated Test Suite**: There is currently no automated testing pipeline setup for the repository.

## Folder Structure
- `scraper/`: Contains the scraping logic and interceptors (e.g., Yatra get-fare interception).
- `db/`: Handles database connections, setup, and defines the raw fare data models (`flight_prices`).
- `cleaning/`: Holds the pipeline to process, deduplicate, and clean raw data, writing to `flight_prices_clean`.
- `index_calc/`: Calculates the Jevons APIx (daily, weekly, monthly) using cleaned data, saving to `airfare_index`.
- `api/`: The FastAPI application that exposes the final indexed and raw data for the frontend dashboards.
