"""Verify bundled native dialog runtime without opening a folder chooser."""
import json
from pathlib import Path
import requests

script = '''import tkinter
_photo_picker_root = tkinter.Tk()
_photo_picker_root.withdraw()
_photo_picker_root.update_idletasks()
_photo_picker_root.destroy()
context['result_payload'] = {'picker_runtime': True}
'''
response = requests.post('http://127.0.0.1:9173/api/local/exec', json={'script': script}, timeout=20)
response.raise_for_status()
data = response.json()
if not data.get('ok') or not data.get('result_payload', {}).get('picker_runtime'):
    raise RuntimeError(str(data)[:1000])
Path('output/photo-series-v2-test/picker-runtime.json').write_text(json.dumps(data['result_payload']), encoding='utf-8')
print('Native folder-picker runtime verified in installed PrintAgent executable.')
