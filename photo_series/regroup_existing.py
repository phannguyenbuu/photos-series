"""Preserve completed work and create a corrected, EXIF/scene-grouped successor job."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from .pipeline import Pipeline, configuration

old_id = sys.argv[1]
directory = Path('output/photo-series-v3-test')
source = Pipeline(directory)
local = Pipeline(Path(os.environ['APPDATA'])/'GoxPrintAgent/photo-series')
response = requests.get('http://127.0.0.1:9173/api/photo-series/jobs/'+old_id, timeout=10)
response.raise_for_status()
old = response.json()
if old.get('running'):
    raise RuntimeError('Original job is still running; do not interrupt it')
backup = directory/(old_id+'.before-regroup.json')
if not backup.exists():
    backup.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding='utf-8')
config_path = local.directory/(old_id+'.json')
cfg = configuration(json.loads(config_path.read_text(encoding='utf-8')))
audit = json.loads((directory/'grouping-audit.json').read_text(encoding='utf-8'))
fresh = source.get('downloads-regroup-v3')
config_stat = config_path.stat()
started_at = old.get('started_at') or getattr(config_stat, 'st_birthtime', config_stat.st_mtime)
previous_elapsed = old.get('elapsed_seconds') or max(0, old['updated_at']-started_at)
old['elapsed_seconds'] = previous_elapsed
old['started_at'] = started_at
old['finished_at'] = old.get('finished_at') or old['updated_at']
contexts = {}
for group in old['series']:
    if group.get('result'):
        for image in group['result'].get('images', []):
            contexts[image['image_id']] = group
new_id = uuid.uuid4().hex
job = dict(job_id=new_id, source_job_id=old_id, folder=old['folder'], status='selected',
    created_at=time.time(), started_at=started_at, elapsed_seconds=previous_elapsed+audit['seconds'],
    total=fresh['total'], total_files=fresh['total_files'], processed=fresh['processed'],
    pipeline_version=3, grouping=fresh['grouping'], errors=fresh['errors'], series=copy.deepcopy(fresh['series']))
reused = 0
for group in job['series']:
    for image in group['images']:
        image['selected'] = image['rank'] <= cfg['n']
    first, last = min(group['images'], key=lambda i: i['sort_time']), max(group['images'], key=lambda i: i['sort_time'])
    group['capture_time_start'], group['capture_time_end'] = first.get('capture_time_original'), last.get('capture_time_original')
    previous = contexts.get(group['representative_image_id'])
    if previous:
        group.update(status='completed', result=copy.deepcopy(previous['result']), result_text=previous.get('result_text', ''),
                     source_context_job_id=previous.get('remote_job_id'), context_reused=True)
        reused += 1
preparing = time.monotonic()
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(local.prepare, job, group, cfg) for group in job['series']]
    for index, future in enumerate(as_completed(futures), 1):
        future.result()
        if index % 20 == 0:
            print(f'Prepared {index}/{len(futures)} corrected groups', flush=True)
job['elapsed_seconds'] += time.monotonic()-preparing
job['reused_contexts'] = reused
(local.directory/(new_id+'.json')).write_text(json.dumps(cfg), encoding='utf-8')
local.save(job)
old['replacement_job_id'] = new_id
local.save(old)
report = dict(previous_job_id=old_id, replacement_job_id=new_id, previous_groups=len(old['series']),
              groups=len(job['series']), reused_contexts=reused, missing_contexts=len(job['series'])-reused)
(directory/'recovery.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
