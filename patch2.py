import re

with open('api/main.py', 'r') as f:
    content = f.read()

# Replace lead_times logic
old_lead_times = '''    lead_times = sorted(
        {lt for lt in TRAINED_LEAD_TIMES if lt <= days_until_departure}
        | {days_until_departure}
    )'''
new_lead_times = '''    max_trained_lead_time = max(TRAINED_LEAD_TIMES)
    book_now_lead_time = min(days_until_departure, max_trained_lead_time)
    lead_times = sorted(
        {lt for lt in TRAINED_LEAD_TIMES if lt <= book_now_lead_time}
        | {book_now_lead_time}
    )'''
content = content.replace(old_lead_times, new_lead_times)

# Delete book_now_lead_time = max(lead_times)
content = content.replace('    book_now_lead_time = max(lead_times)\n', '')

# Move metadata and add new response fields
old_return_block = '''    metadata = get_model_metadata() or {}
    return PredictCurveResponse(
        route=route,
        airline=airline,
        cabin_class=cabin_class,
        departure_date=departure_date,
        curve=curve,
        cheapest_lead_time_days=cheapest.lead_time_days,
        cheapest_fare=cheapest.predicted_fare,
        recommendation=recommendation,
        model_trained_at=metadata.get("trained_at"),
    )'''

new_return_block = '''    metadata = get_model_metadata() or {}
    
    extrapolation_note = None
    if days_until_departure > max_trained_lead_time:
        extrapolation_note = (
            f"Departure is {days_until_departure} days away, beyond the "
            f"{max_trained_lead_time}-day maximum lead time in the training data. "
            f"The curve starts at {max_trained_lead_time} days."
        )

    mae = (metadata.get("test_metrics") or {}).get("mae")
    fare_spread = max(p.predicted_fare for p in curve) - min(
        p.predicted_fare for p in curve
    )
    confidence_note = None
    if mae is not None and fare_spread < mae:
        confidence_note = (
            f"Predicted fares across this curve vary by about INR {fare_spread:.0f}, "
            f"which is smaller than the model's average error of INR {mae:.0f}. "
            f"Treat the timing recommendation as weak for this flight."
        )

    return PredictCurveResponse(
        route=route,
        airline=airline,
        cabin_class=cabin_class,
        departure_date=departure_date,
        curve=curve,
        cheapest_lead_time_days=cheapest.lead_time_days,
        cheapest_fare=cheapest.predicted_fare,
        recommendation=recommendation,
        model_trained_at=metadata.get("trained_at"),
        extrapolation_note=extrapolation_note,
        model_test_mae=mae,
        confidence_note=confidence_note,
    )'''

content = content.replace(old_return_block, new_return_block)

with open('api/main.py', 'w') as f:
    f.write(content)
