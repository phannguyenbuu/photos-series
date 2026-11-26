"""Run on the bridge VPS: list grid batches of one local photo job with timing and per-cell outcome."""
import json
import sqlite3
import sys
import urllib.request

con = sqlite3.connect("/var/www/chatgpt_bridge/photo-series-state/series.sqlite3")
rows = con.execute("SELECT batch_id,remote_job_id,members,created FROM grid_batches WHERE local_job_id=? ORDER BY created", (sys.argv[1],)).fetchall()
t0 = rows[0][3] if rows else 0
for batch_id, remote, members, created in rows:
    cells = json.loads(members)
    states = []
    for grid_id, _ in cells:
        row = con.execute("SELECT result FROM requests WHERE job_id=?", (grid_id,)).fetchone()
        states.append(json.loads(row[0])["status"] if row and row[0] else "retried")
    try:
        job = json.load(urllib.request.urlopen("http://127.0.0.1:5000/api/jobs/"+remote, timeout=5))
        duration, status = job.get("duration_seconds"), job["status"]
    except Exception:
        duration, status = None, "?"
    print(f"+{created-t0:6.1f}s cells {len(cells):2d} request {status} {duration}s "
          f"ok {states.count('completed')} failed {states.count('failed')} retried {states.count('retried')}")
