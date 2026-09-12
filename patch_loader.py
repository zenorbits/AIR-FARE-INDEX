import sys

with open('ml/data_loader.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('df = pd.read_sql(query, engine)', 'df = pd.read_sql(query, con=engine)')

with open('ml/data_loader.py', 'w', encoding='utf-8') as f:
    f.write(content)
