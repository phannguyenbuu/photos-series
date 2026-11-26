"""Run on VPS: update the series adapter only when the shared queue is idle."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import subprocess
import time
import urllib.request

source = Path('/tmp/photo-series-bridge-next.py')
target = Path('/var/www/chatgpt_bridge/photo_series/bridge_routes.py')
compile(source.read_text(encoding='utf-8'), str(target), 'exec')
state = json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=10))
if state['active_jobs'] or state['queue_length']:
    raise RuntimeError('Bridge queue is not idle; defer update')
backup = Path('/var/backups')/('photo-series-cancel-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
shutil.copy2(target, backup/target.name)
candidate = target.with_name('bridge_routes.next.py')
shutil.copy2(source, candidate)
os.replace(candidate, target)
try:
    subprocess.run(['systemctl', 'restart', 'chatgpt-bridge'], check=True)
    for attempt in range(30):
        try:
            check = json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=2))
            if check['status'] == 'online':
                print('Bridge adapter updated; backup:', backup)
                break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('Bridge health check failed')
except BaseException:
    shutil.copy2(backup/target.name, target)
    subprocess.run(['systemctl', 'restart', 'chatgpt-bridge'], check=True)
    raise
