"""
Unit tests for the Jevons index calculation module.
"""
import pytest
from datetime import datetime
from index_calc.jevons import (
    get_geometric_mean,
    get_weekly_period,
    get_monthly_period,
    ROUTE_WEIGHTS
)

def test_geometric_mean_known_values():
    assert get_geometric_mean([2, 8]) == pytest.approx(4.0)
    assert get_geometric_mean([1, 10, 100]) == pytest.approx(10.0)

def test_geometric_mean_single_value():
    assert get_geometric_mean([42]) == pytest.approx(42.0)

def test_geometric_mean_realistic_fares():
    assert get_geometric_mean([5000, 5000, 5000]) == pytest.approx(5000.0)

# Hand calculation for Jevons ratio:
# Base fares: [4000, 5000, 6250]
# Geometric mean of base: (4000 * 5000 * 6250) ^ (1/3)
# = (125,000,000,000) ^ (1/3) = 5000.0
# Current fares: [4800, 6000, 7500] (which is exactly 1.2x each base fare)
# Geometric mean of current: (4800 * 6000 * 7500) ^ (1/3)
# = (216,000,000,000) ^ (1/3) = 6000.0
# Jevons index: (6000.0 / 5000.0) * 100 = 120.0
def test_jevons_ratio_hand_calculated():
    base_fares = [4000, 5000, 6250]
    current_fares = [4800, 6000, 7500]
    
    b_price = get_geometric_mean(base_fares)
    c_geomean = get_geometric_mean(current_fares)
    
    idx_val = 100.0 * (c_geomean / b_price)
    assert idx_val == pytest.approx(120.0)

def test_weekly_period_boundaries():
    # September 14, 2026 is a Monday (start of week)
    dt1 = datetime(2026, 9, 14, 10, 0, 0)
    # September 20, 2026 is a Sunday (end of week)
    dt2 = datetime(2026, 9, 20, 23, 59, 59)
    # September 21, 2026 is next Monday
    dt_next = datetime(2026, 9, 21, 0, 0, 1)
    
    period1 = get_weekly_period(dt1)
    period2 = get_weekly_period(dt2)
    period_next = get_weekly_period(dt_next)
    
    assert period1 == period2
    assert period1 != period_next

def test_monthly_period_boundaries():
    # Start of month
    dt1 = datetime(2026, 9, 1, 0, 0, 1)
    # End of month
    dt2 = datetime(2026, 9, 30, 23, 59, 59)
    # Start of next month
    dt_next = datetime(2026, 10, 1, 0, 0, 1)
    
    period1 = get_monthly_period(dt1)
    period2 = get_monthly_period(dt2)
    period_next = get_monthly_period(dt_next)
    
    assert period1 == period2
    assert period1 != period_next

def test_route_weights_sum():
    assert len(ROUTE_WEIGHTS) == 6
    weights_sum = sum(ROUTE_WEIGHTS.values())
    assert weights_sum == pytest.approx(1.0, abs=0.01)
