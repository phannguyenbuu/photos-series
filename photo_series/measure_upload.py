"""Measure real multipart request throughput using completed, idempotent uploads."""
import json
import time
from contextlib import ExitStack
import requests
from .pipeline import Pipeline, configuration


def main():
    pipeline = Pipeline('output/photo-series-v2-test')
    job = pipeline.get('downloads-remote-v2')
    cfg = configuration({})
    prepared = [pipeline.prepare(job, group, cfg) for group in job['series']]
    results = []
    with requests.Session() as session:
        for metadata, files in prepared:
            with ExitStack() as stack:
                parts = [(field, (path.name, stack.enter_context(path.open('rb')), 'image/webp')) for field, path in files]
                request = requests.Request('POST', cfg['server_url']+'/api/vision/analyze',
                    data={'metadata': json.dumps(metadata)}, files=parts, headers=pipeline.headers(cfg))
                request = session.prepare_request(request)
                size = int(request.headers['Content-Length'])
                started = time.perf_counter()
                response = session.send(request, timeout=cfg['timeout'])
                seconds = time.perf_counter()-started
            response.raise_for_status()
            if response.status_code != 200:
                raise RuntimeError('Expected an existing completed upload; stopping benchmark')
            results.append(dict(series_id=metadata['series_id'], bytes=size, seconds=round(seconds, 4),
                megabytes_per_second=round(size/seconds/1_000_000, 3), megabits_per_second=round(size*8/seconds/1_000_000, 3),
                http_status=response.status_code))
    size = sum(item['bytes'] for item in results)
    seconds = sum(item['seconds'] for item in results)
    report = dict(requests=results, total_bytes=size, total_seconds=round(seconds, 4),
        effective_MB_per_second=round(size/seconds/1_000_000, 3), effective_Mbps=round(size*8/seconds/1_000_000, 3),
        scope='Client send to full HTTP acknowledgement; includes TLS/latency/server validation, excludes image preparation and AI inference. Sequential requests reuse a session; duplicate uploads do not create inference jobs.')
    (pipeline.directory/'upload-speed.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
