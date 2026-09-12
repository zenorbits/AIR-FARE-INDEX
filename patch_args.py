import sys

# FIX ml/features.py
with open('ml/features.py', 'r', encoding='utf-8') as f:
    content = f.read()

bad_def = '''def build_single_prediction_row(
    route: str,
    airline: str,
    cabin_class: str,
    source: str = "cleartrip",
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
) -> pd.DataFrame:'''
good_def = '''def build_single_prediction_row(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
    source: str = "cleartrip",
) -> pd.DataFrame:'''
content = content.replace(bad_def, good_def)

with open('ml/features.py', 'w', encoding='utf-8') as f:
    f.write(content)


# FIX ml/predict.py
with open('ml/predict.py', 'r', encoding='utf-8') as f:
    content = f.read()

bad_predict_def = '''def predict_fare(
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
good_predict_def = '''def predict_fare(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
    source: str = "cleartrip",
) -> float:'''
content = content.replace(bad_predict_def, good_predict_def)

with open('ml/predict.py', 'w', encoding='utf-8') as f:
    f.write(content)
