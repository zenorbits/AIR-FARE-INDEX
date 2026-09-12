import sys

with open('ml/features.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update docstring
old_docstring = '''\"\"\"
Shared cleaning / feature-engineering logic for the Price Prediction feature.

This module is imported by BOTH ml/train.py (offline training) and
ml/predict.py (online inference via the API), so that the exact same
transformations are applied at train time and at prediction time.

Target variable: total_fare
Excluded predictors (target leakage): total_fare, base_fare, taxes_fees
\"\"\"'''
new_docstring = '''\"\"\"
Shared cleaning / feature-engineering logic for the Price Prediction feature.

This module is imported by BOTH ml/train.py (offline training) and
ml/predict.py (online inference via the API), so that the exact same
transformations are applied at train time and at prediction time.

Target variable: total_fare
Excluded predictors (target leakage): total_fare, base_fare, taxes_fees

Note: source is included as a feature because Yatra returns only the cheapest
fare per airline while Cleartrip returns every flight, meaning the two sources
have systematically different fare distributions.
\"\"\"'''
content = content.replace(old_docstring, new_docstring)

# REQUIRED_RAW_COLUMNS
old_req = '''REQUIRED_RAW_COLUMNS = [
    "route",
    "flight_number",
    "airline",
    "cabin_class",
    "stops",
    "lead_time_days",
    "departure_time",
]'''
new_req = '''REQUIRED_RAW_COLUMNS = [
    "route",
    "flight_number",
    "airline",
    "cabin_class",
    "source",
    "stops",
    "lead_time_days",
    "departure_time",
]'''
content = content.replace(old_req, new_req)

# CATEGORICAL_FEATURES
content = content.replace('CATEGORICAL_FEATURES = ["route", "airline", "cabin_class"]', 'CATEGORICAL_FEATURES = ["route", "airline", "cabin_class", "source"]')

# clean_dataframe
old_clean = '''    # 4. Normalize cabin_class
    out["cabin_class"] = out["cabin_class"].astype(str).str.strip().str.upper()'''
new_clean = '''    # 4. Normalize cabin_class
    out["cabin_class"] = out["cabin_class"].astype(str).str.strip().str.upper()
    out["source"] = out["source"].astype(str).str.strip().str.lower()'''
content = content.replace(old_clean, new_clean)

# build_single_prediction_row
old_build_def = '''def build_single_prediction_row(
    route: str,
    airline: str,
    cabin_class: str,
    lead_time_days: int,
    stops: int,
    departure_hour: int,
    departure_day_of_week: int,
    departure_month: int,
) -> pd.DataFrame:'''
new_build_def = '''def build_single_prediction_row(
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
content = content.replace(old_build_def, new_build_def)

old_row_dict = '''    row = {
        "route": str(route).strip().upper(),
        "airline": str(airline).strip(),
        "cabin_class": str(cabin_class).strip().upper(),
        "stops": int(stops),
        "lead_time_days": int(lead_time_days),'''
new_row_dict = '''    row = {
        "route": str(route).strip().upper(),
        "airline": str(airline).strip(),
        "cabin_class": str(cabin_class).strip().upper(),
        "source": str(source).strip().lower(),
        "stops": int(stops),
        "lead_time_days": int(lead_time_days),'''
content = content.replace(old_row_dict, new_row_dict)

with open('ml/features.py', 'w', encoding='utf-8') as f:
    f.write(content)
