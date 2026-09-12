import sys

with open('ml/predict.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_predict_def = '''def predict_fare(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
) -> float:'''
new_predict_def = '''def predict_fare(
    route: str,
    airline: str,
    cabin_class: str,
    source: str = "cleartrip",
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
) -> float:'''
content = content.replace(old_predict_def, new_predict_def)

old_row = '''    row = build_single_prediction_row(
        route=route,
        airline=normalized_airline,
        cabin_class=cabin_class,
        lead_time_days=lead_time_days,'''
new_row = '''    row = build_single_prediction_row(
        route=route,
        airline=normalized_airline,
        cabin_class=cabin_class,
        source=source,
        lead_time_days=lead_time_days,'''
content = content.replace(old_row, new_row)

# Also update the docstring of predict_fare because it mentions RandomForestRegressor
content = content.replace('using the trained\n    RandomForestRegressor pipeline', 'using the trained\n    HistGradientBoostingRegressor pipeline')

with open('ml/predict.py', 'w', encoding='utf-8') as f:
    f.write(content)
