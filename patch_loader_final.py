import sys

with open('ml/data_loader.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_load_db = '''    from db.database import get_engine
    from cleaning.pipeline import FlightPriceClean

    engine = get_engine()
    query = "SELECT * FROM flight_prices_clean"
    df = pd.read_sql(query, engine.raw_connection())'''

new_load_db = '''    from sqlalchemy import text
    from db.database import get_engine
    from cleaning.pipeline import FlightPriceClean

    engine = get_engine()
    query = "SELECT * FROM flight_prices_clean"
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)'''
content = content.replace(old_load_db, new_load_db)

with open('ml/data_loader.py', 'w', encoding='utf-8') as f:
    f.write(content)
