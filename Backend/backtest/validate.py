"""
Validation module for APIx.

The benchmark is the official CPI Airfare item index (COICOP code 07.3.3.1.2.01, base year 2024).
Route-level DGCA average-fare data is not publicly available, which is why CPI is used as the comparison series.
"""

import logging
import pandas as pd
from index_calc.jevons import ROUTE_WEIGHTS, get_geometric_mean, get_engine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_cpi_benchmark(path="data/cpi_airfare_index.xlsx"):
    df = pd.read_excel(path)
    df = df[(df['state'] == 'All India') & (df['sector'] == 'Combined')].copy()
    
    # Build period_start date column from year and month
    df['period_start'] = pd.to_datetime(df['year'].astype(str) + ' ' + df['month'], format="%Y %B")
    
    res = df[['period_start', 'index', 'inflation']].copy()
    res.rename(columns={'index': 'cpi_index', 'inflation': 'cpi_inflation'}, inplace=True)
    res.sort_values(by='period_start', ascending=True, inplace=True)
    return res.reset_index(drop=True)

def load_apix_monthly(engine):
    from sqlalchemy import text
    query = text("SELECT route, lead_time_days, period_start, index_value FROM airfare_index WHERE frequency = 'monthly' AND route IS NOT NULL AND lead_time_days IS NOT NULL")
    with engine.connect() as conn:
        result = conn.execute(query)
        df = pd.DataFrame(result.fetchall(), columns=list(result.keys()))
    
    if df.empty:
        return pd.DataFrame(columns=['period_start', 'apix_index'])

    df['period_start'] = pd.to_datetime(df['period_start'])
    
    records = []
    # Aggregate across route and lead_time_days
    for period, p_df in df.groupby('period_start'):
        route_indices = {}
        for route, r_df in p_df.groupby('route'):
            route_indices[route] = get_geometric_mean(r_df['index_value'].tolist())
            
        present_routes = list(route_indices.keys())
        raw_weights = {r: ROUTE_WEIGHTS[r] for r in present_routes if r in ROUTE_WEIGHTS}
        if not raw_weights:
            continue
        weight_sum = sum(raw_weights.values())
        
        overall_index = 0.0
        for r, r_idx in route_indices.items():
            if r in raw_weights:
                overall_index += r_idx * (raw_weights[r] / weight_sum)
                
        records.append({'period_start': period, 'apix_index': overall_index})
        
    res = pd.DataFrame(records)
    if not res.empty:
        res.sort_values(by='period_start', ascending=True, inplace=True)
        res.reset_index(drop=True, inplace=True)
    return res

def compare(cpi_df, apix_df):
    if apix_df.empty or cpi_df.empty:
        merged = pd.DataFrame()
        return merged, "Not enough overlapping periods for stats."
        
    merged = pd.merge(cpi_df, apix_df, on='period_start', how='inner')
    if merged.empty:
        return merged, "Not enough overlapping periods for stats."
        
    merged['abs_diff'] = merged['apix_index'] - merged['cpi_index']
    merged['pct_diff'] = (merged['abs_diff'] / merged['cpi_index']) * 100
    
    if len(merged) >= 3:
        corr = merged['cpi_index'].corr(merged['apix_index'])
        mad = merged['abs_diff'].abs().mean()
        stats_dict = {'correlation': corr, 'mean_absolute_deviation': mad}
        return merged, stats_dict
    else:
        return merged, "Overlap is too short for correlation (need at least 3 periods)."

def run_backtest():
    engine = get_engine()
    
    logger.info("Loading CPI benchmark data...")
    cpi_df = load_cpi_benchmark()
    
    logger.info("Loading monthly APIx data from DB...")
    apix_df = load_apix_monthly(engine)
    
    if apix_df.empty:
        logger.warning("No monthly APIx data found in database. Exiting backtest cleanly.")
        return pd.DataFrame()
        
    logger.info("Comparing CPI and APIx...")
    merged, stats = compare(cpi_df, apix_df)
    
    print("\n--- Backtest Comparison Table ---")
    if merged.empty:
        print("No overlapping periods found between CPI and APIx.")
    else:
        display_df = merged.copy()
        display_df['period_start'] = display_df['period_start'].dt.strftime('%Y-%m')
        display_df['cpi_index'] = display_df['cpi_index'].round(2)
        display_df['apix_index'] = display_df['apix_index'].round(2)
        display_df['abs_diff'] = display_df['abs_diff'].round(2)
        display_df['pct_diff'] = display_df['pct_diff'].round(2)
        print(display_df.to_string(index=False))
        
    print("\n--- Statistics ---")
    if isinstance(stats, dict):
        print(f"Pearson Correlation: {stats['correlation']:.4f}")
        print(f"Mean Absolute Deviation: {stats['mean_absolute_deviation']:.2f}")
    else:
        print(stats)
        
    return merged

if __name__ == "__main__":
    run_backtest()
