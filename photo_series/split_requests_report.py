"""Run on the bridge VPS: show people-split requests of one local job with timing and raw answer."""
import json
import sqlite3
import sys
import urllib.request

con = sqlite3.connect("/var/www/chatgpt_bridge/photo-series-state/series.sqlite3")
for split_id, clusters, remote, result in con.execute(
        "SELECT split_id,clusters,remote_job_id,result FROM split_requests WHERE request_key LIKE ? ORDER BY rowid", (sys.argv[1]+":%",)):
    clusters = json.loads(clusters)
    try:
        job = json.load(urllib.request.urlopen("http://127.0.0.1:5000/api/jobs/"+remote, timeout=5))
    except Exception:
        job = {}
    outcome = json.loads(result) if result else {}
    usable = sum(g is not None for g in outcome.get("groups") or [])
    print(f"{split_id} clusters {len(clusters)} tiles {sum(map(len, clusters))} request {job.get('status')} {job.get('duration_seconds')}s "
          f"step={job.get('current_step')!r} -> {outcome.get('status')} usable {usable}/{len(clusters)}")
    print("   raw:", (job.get("result_text") or "")[:300].replace("\n", " "))
