import pytest
import pandas as pd
from backtest.validate import compare

def test_compare_computes_diffs():
    cpi_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01']),
        'cpi_index': [100.0, 105.0, 110.0, 115.0]
    })
    apix_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01']),
        'apix_index': [102.0, 104.0, 112.0, 115.0]
    })
    
    merged, stats = compare(cpi_df, apix_df)
    
    # Assert diffs are computed correctly for at least one row
    row_0 = merged.iloc[0]
    assert row_0['abs_diff'] == 2.0  # 102.0 - 100.0
    assert row_0['pct_diff'] == (2.0 / 100.0) * 100  # 2.0
    
    assert isinstance(stats, dict)
    assert 'correlation' in stats
    assert 'mean_absolute_deviation' in stats


def test_compare_short_overlap_returns_note():
    cpi_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01']),
        'cpi_index': [100.0, 105.0]
    })
    apix_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01']),
        'apix_index': [102.0, 104.0]
    })
    
    merged, stats = compare(cpi_df, apix_df)
    
    assert len(merged) == 2
    assert isinstance(stats, str)


def test_compare_no_overlap():
    cpi_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01']),
        'cpi_index': [100.0, 105.0]
    })
    apix_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-03-01', '2026-04-01']),
        'apix_index': [102.0, 104.0]
    })
    
    merged, stats = compare(cpi_df, apix_df)
    
    assert merged.empty
    assert isinstance(stats, str)


def test_compare_empty_input():
    cpi_df = pd.DataFrame({
        'period_start': pd.to_datetime(['2026-01-01', '2026-02-01']),
        'cpi_index': [100.0, 105.0]
    })
    apix_df = pd.DataFrame(columns=['period_start', 'apix_index'])
    
    merged, stats = compare(cpi_df, apix_df)
    
    assert merged.empty
    assert isinstance(stats, str)
