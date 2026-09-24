import os
from config import Config
import psycopg2

from dotenv import load_dotenv
from db import DATABASE_URL
load_dotenv()
conn = psycopg2.connect(DATABASE_URL.replace('postgresql+psycopg2://', 'postgresql://'))
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.columns WHERE column_name = 'id' AND table_schema = 'social_media_agent' AND data_type = 'integer';")
tables = [r[0] for r in cur.fetchall()]
print('Tables:', tables)

for table in tables:
    cur.execute(f"SELECT MAX(id) FROM social_media_agent.{table};")
    max_id = cur.fetchone()[0] or 0
    if max_id > 0:
        cur.execute(f"SELECT setval(pg_get_serial_sequence('social_media_agent.{table}', 'id'), {max_id});")
        print(f"Set sequence for {table} to {max_id}")

conn.commit()
cur.close()
conn.close()
print("Done!")
