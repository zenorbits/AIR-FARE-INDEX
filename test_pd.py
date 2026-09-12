import sys
import pandas as pd
from db.database import get_engine
import sqlalchemy

engine = get_engine()
print('SQLAlchemy version:', sqlalchemy.__version__)
print('Pandas version:', pd.__version__)
print('Engine type:', type(engine))

from pandas.io.sql import pandasSQL_builder
pandas_sql = pandasSQL_builder(engine)
print('pandas_sql type:', type(pandas_sql))

