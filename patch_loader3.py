import sys

with open('ml/data_loader.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('with engine.connect() as conn:\n        df = pd.read_sql(query, con=conn)', 'df = pd.read_sql(query, engine.raw_connection())')

with open('ml/data_loader.py', 'w', encoding='utf-8') as f:
    f.write(content)
