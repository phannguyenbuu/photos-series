"""Patch photo JSON completion and restart only existing idle automation sessions."""
import json
from pathlib import Path
import subprocess
import urllib.request

state = json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=10))
if state['active_jobs'] or state['queue_length']:
    raise RuntimeError('Active bridge work exists; defer extension update')
for slot in range(21, 41):
    directory = Path('/opt/genai-desktop')/('isolated-extension-'+str(slot))
    if (directory/'content.js').exists():
        subprocess.run(['python3', '/tmp/patch_complete_json.py', str(directory)], check=True)
# Only the established automation workers are restarted. Profiles and sign-in data stay intact.
units = ['genai-isolated@'+str(slot)+'.service' for slot in range(21, 29)
         if subprocess.run(['systemctl', 'is-active', '--quiet', 'genai-isolated@'+str(slot)+'.service']).returncode == 0]
if units:
    subprocess.run(['systemctl', 'restart', *units], check=True)
print('Updated complete-JSON automation workers:', len(units))
