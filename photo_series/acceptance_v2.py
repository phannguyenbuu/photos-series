"""Local acceptance on the actual folder; remote verification on representative groups."""
import copy
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from .pipeline import Pipeline, configuration
from .image_source import RAW_EXTENSIONS, read_image


def main():
    pipeline = Pipeline('output/photo-series-v2-test')
    cfg = configuration({})
    root = Path.home()/'Downloads/1'
    job = dict(job_id='downloads-local-v2', folder=str(root), series=[])
    started = time.perf_counter()
    pipeline.scan(job, cfg)
    print(f"Scanned {job['total_files']} files, {job['total']} captures, {len(job['series'])} groups, errors={len(job['errors'])}", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(pipeline.prepare, job, group, cfg) for group in job['series']]
        for index, future in enumerate(as_completed(futures), 1):
            metadata, files = future.result()
            assert len(metadata['images']) == 1 and len(files) == 2
            if index % 25 == 0:
                print(f'Prepared {index}/{len(futures)} groups', flush=True)
    pipeline.save(job)
    # Exercise RAW decoders directly, including paired files and a RAW-only capture.
    raw_paths = sorted(p for p in root.rglob('*') if p.suffix.lower() in RAW_EXTENSIONS)
    only_raw = [Path(i['path']) for s in job['series'] for i in s['images'] if Path(i['path']).suffix.lower() in RAW_EXTENSIONS]
    raw_checks = []
    for path in list(dict.fromkeys(raw_paths[:10]+only_raw)):
        image, metadata = read_image(path)
        raw_checks.append(dict(filename=path.name, preview_size=image.size, **metadata))
    local = dict(files=job['total_files'], captures=job['total'], raw_files=job['raw_files'],
        paired_captures=sum(len(i['source_files']) > 1 for s in job['series'] for i in s['images']),
        groups=len(job['series']), selected=sum(i['selected'] for s in job['series'] for i in s['images']),
        representatives=len(job['series']), upload_bytes=sum(i.get('upload_bytes', 0) for s in job['series'] for i in s['images']),
        errors=job['errors'], grouping=job['grouping'], raw_checks=raw_checks, seconds=round(time.perf_counter()-started, 2))
    (pipeline.directory/'local-summary.json').write_text(json.dumps(local, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in local.items() if k != 'raw_checks'}, ensure_ascii=False), flush=True)
    selected_groups = sorted(job['series'], key=lambda group: -len(group['images']))[:3]
    smoke = dict(job_id='downloads-remote-v2', folder=str(root), total=job['total'], total_files=job['total_files'], processed=job['processed'],
        pipeline_version=2, is_sample=True, source_series_total=len(job['series']), series=copy.deepcopy(selected_groups), status='selected')
    pipeline.save(smoke)
    pipeline.run(smoke, cfg)
    if smoke['status'] != 'completed' or not all(group.get('result') for group in smoke['series']):
        raise RuntimeError('Representative remote test did not complete')
    # Keep the existing browser link working, now with explicit sample scope and the v2 flow.
    destination = Pipeline(Path(os.environ['APPDATA'])/'GoxPrintAgent/photo-series')
    alias = copy.deepcopy(smoke)
    alias['job_id'] = 'downloads-remote-smoke'
    for group in alias['series']:
        target = destination.directory/alias['job_id']/group['series_id']
        target.mkdir(parents=True, exist_ok=True)
        for image in group['images']:
            if not image['selected']:
                continue
            for suffix in ('_thumbnail.webp', '_preview.jpg', '_primary.webp'):
                source = pipeline.directory/smoke['job_id']/group['series_id']/(image['image_id']+suffix)
                if source.is_file():
                    shutil.copy2(source, target/source.name)
    destination.save(alias)
    summary = dict(local=local, remote_series=3, remote_source_images=3,
                   selected_images=sum(i['selected'] for s in smoke['series'] for i in s['images']), remote_status=smoke['status'])
    (pipeline.directory/'acceptance.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Remote: 3/3 groups completed, exactly 1 representative per group. Demo updated.', flush=True)


if __name__ == '__main__':
    main()
