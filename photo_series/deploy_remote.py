"""Run on the AI VPS after uploading the adapter and built UI to staging."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

staging = Path(__file__).resolve().parent
root = Path('/var/www/chatgpt_bridge')
ui = Path('/var/www/genai.toolxprint.com/dist')
server = root/'server.py'
text = server.read_text(encoding='utf-8')
anchor = 'orchestrator = Orchestrator()'
addition = anchor + "\nfrom photo_series.bridge_routes import register_photo_series_bridge\nregister_photo_series_bridge(app, orchestrator, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'photo-series-state'))"
if addition not in text:
    if text.count(anchor) != 1:
        raise RuntimeError('Bridge anchor mismatch')
    text = text.replace(anchor, addition)
compile(text, str(server), 'exec')
status = json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=10))
if status.get('active_jobs', 0) or status.get('queue_length', 0):
    raise RuntimeError('Bridge has active jobs; deploy deferred')
backup = Path('/var/backups')/('photo-series-'+time.strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
shutil.copy2(server, backup/'server.py')
shutil.copy2(ui/'index.html', backup/'index.html')
module = root/'photo_series'
if module.exists():
    shutil.copytree(module, backup/'photo_series')
module.mkdir(exist_ok=True)
for name in ('__init__.py', 'bridge_routes.py'):
    shutil.copy2(staging/'photo_series'/name, module/name)
candidate = root/'server.photo-series-next.py'
candidate.write_text(text, encoding='utf-8')
os.replace(candidate, server)
try:
    subprocess.run(['systemctl', 'restart', 'chatgpt-bridge'], check=True)
    for attempt in range(30):
        try:
            health = json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=2))
            if health.get('status') == 'online':
                break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('Bridge health check failed')
    for source in (staging/'dist/assets').iterdir():
        if source.is_file():
            shutil.copy2(source, ui/'assets'/source.name)
    shutil.copy2(staging/'dist/index.html', ui/'index.html')
except BaseException:
    shutil.copy2(backup/'server.py', server)
    shutil.copy2(backup/'index.html', ui/'index.html')
    subprocess.run(['systemctl', 'restart', 'chatgpt-bridge'], check=True)
    raise
print(json.dumps(dict(deployed=True, backup=str(backup), bridge=health['status'])))
