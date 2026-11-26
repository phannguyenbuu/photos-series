"""Avoid interrupting an active photo job when replacing PrintAgent."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import requests

database = Path(os.environ['APPDATA'])/'GoxPrintAgent/photo-series/state.sqlite3'
if database.exists():
    con = sqlite3.connect(database, timeout=5)
    try:
        records = [json.loads(row[0]) for row in con.execute('SELECT state FROM jobs')]
    finally:
        con.close()
    for job in records:
        if job.get('status') not in ('pending', 'scanning', 'preparing', 'uploading', 'analyzing', 'cancelling'):
            continue
        try:
            response = requests.get('http://127.0.0.1:9173/api/photo-series/jobs/'+job['job_id'], timeout=3)
            if response.ok and response.json().get('running'):
                print('PrintAgent has an active photo job; restart deferred:', job['job_id'])
                sys.exit(42)
        except requests.ConnectionError:
            break
