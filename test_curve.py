import os
os.environ['API_KEY'] = 'test-api-key'

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app, headers={'X-API-Key': 'test-api-key'})

# Test 1: Full curve (2026-11-15)
print('--- Test 1: Full curve (2026-11-15) ---')
r = client.get('/predict-price/curve?route=DEL-BOM&departure_date=2026-11-15&departure_hour=10&airline=6E')
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Curve:', [(p['lead_time_days'], p['predicted_fare'], p['book_by_date']) for p in data['curve']])
    print('Extrapolation Note:', data.get('extrapolation_note'))
    print('Confidence Note:', data.get('confidence_note'))
    print('Recommendation:', data.get('recommendation'))
else:
    print(r.text)

# Test 2: Past date
print('\n--- Test 2: Past date (2026-09-01) ---')
r = client.get('/predict-price/curve?route=DEL-BOM&departure_date=2026-09-01&departure_hour=10&airline=6E')
print('Status:', r.status_code)
if r.status_code != 200:
    print('Error detail:', r.json().get('detail'))

# Test 3: 10 days out (2026-09-23)
print('\n--- Test 3: 10 days out (2026-09-23) ---')
r = client.get('/predict-price/curve?route=DEL-BOM&departure_date=2026-09-23&departure_hour=10&airline=6E')
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Curve:', [(p['lead_time_days'], p['predicted_fare'], p['book_by_date']) for p in data['curve']])
    print('Extrapolation Note:', data.get('extrapolation_note'))
    print('Confidence Note:', data.get('confidence_note'))

