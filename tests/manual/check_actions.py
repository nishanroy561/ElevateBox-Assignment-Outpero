"""Quick verification: latest actions rows across all types.

Run with:  python tests/manual/check_actions.py
"""
import os
from dotenv import load_dotenv
load_dotenv(".env")
import psycopg

with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, call_id, type, status, sent_at, attempts,
                   LEFT(COALESCE(last_error, ''), 80) AS err
            FROM actions
            ORDER BY id DESC
            LIMIT 8
        """)
        rows = cur.fetchall()

print(f"{'id':>4} {'call':>4} {'type':<26} {'status':<8} {'sent_at':<28} {'attempts':>3} err")
print("-" * 100)
for r in rows:
    id_, call_id, type_, status, sent_at, attempts, err = r
    sent_str = sent_at.strftime("%Y-%m-%d %H:%M:%S %Z") if sent_at else "None"
    print(f"{id_:>4} {call_id:>4} {type_:<26} {status:<8} {sent_str:<28} {attempts:>3} {err}")
