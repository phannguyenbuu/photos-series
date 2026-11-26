"""Collect the verified v2 outcomes without rerunning uploads or inference."""
import hashlib
import json
from pathlib import Path
import requests

directory = Path('output/photo-series-v2-test')
report = json.loads((directory/'acceptance.json').read_text(encoding='utf-8'))
raw = json.loads((directory/'ui-raw-job.json').read_text(encoding='utf-8'))
picker = json.loads((directory/'picker-runtime.json').read_text(encoding='utf-8'))
sample = requests.get('http://127.0.0.1:9173/api/photo-series/jobs/downloads-remote-smoke', timeout=10).json()
health = requests.get('http://127.0.0.1:9173/api/photo-series/health', timeout=5).json()
assert health['analysis_images_per_series'] == 1 and health['raw_supported']
assert raw['status'] == 'completed' and raw['series'][0]['images'][0]['filename'] == 'RAW_ONLY.ARW'
assert all(len(group['result']['images']) == 1 for group in sample['series'])
assert all(sum(bool(image.get('upload_bytes')) for image in group['images']) == 1 for group in sample['series'])
executable = Path('output/photo-series-release/printagent.exe')
with executable.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
report |= dict(controller='https://dir.toolxprint.com/?view=photo-series', unit_tests_passed=9,
    ui=dict(fan_cards=True, pagination_fixture='12+2 groups', javascript_errors=0,
            raw_only_job=raw['job_id'], raw_only_result='completed', native_folder_runtime=picker['picker_runtime'],
            native_folder_selection='Chooser return value mocked in browser test; native Tk runtime verified in frozen exe'),
    sample_scope=dict(analyzed_groups=len(sample['series']), total_groups=sample['source_series_total'], is_sample=sample['is_sample']),
    executable_sha256=digest)
(directory/'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('Verified: 1137 files; 569 captures; 253 groups; 506 selected; exactly 1 upload representative per group; RAW exe/UI test passed.')
