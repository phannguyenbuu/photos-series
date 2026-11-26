"""Real photo acceptance report; safe, read-only source access."""
import html
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from .pipeline import Pipeline, configuration


def main():
    directory = Path(__file__).resolve().parent.parent / "output" / "photo-series-downloads-test"
    pipeline = Pipeline(directory)
    cfg = configuration(dict(n=3))
    job = dict(job_id="downloads-n3", folder=str(Path.home()/"Downloads/1"), series=[])
    started = time.perf_counter()
    pipeline.scan(job, cfg)
    scan_seconds = time.perf_counter()-started
    print(f"Scanned {job['processed']} photos, {len(job['series'])} series, {len(job['errors'])} errors in {scan_seconds:.1f}s", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(pipeline.prepare, job, group, cfg) for group in job["series"]]
        for index, future in enumerate(as_completed(futures), 1):
            future.result()
            if index % 10 == 0:
                print(f"Prepared {index}/{len(job['series'])} series", flush=True)
    pipeline.save(job)
    selected = [image for group in job["series"] for image in group["images"] if image["selected"]]
    original_bytes = sum(i["bytes"] for group in job["series"] for i in group["images"])
    upload_bytes = sum(i["upload_bytes"] for i in selected)
    stats = dict(images=job["processed"], series=len(job["series"]), selected=len(selected), n=3,
                 errors=job["errors"], scan_seconds=round(scan_seconds, 2), total_seconds=round(time.perf_counter()-started, 2),
                 original_bytes=original_bytes, upload_bytes=upload_bytes, saved_percent=round(100*(1-upload_bytes/original_bytes), 2),
                 raw_files_skipped=len(list(Path(job["folder"]).rglob("*.ARW"))))
    (directory/"summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for index, group in enumerate(job["series"], 1):
        pictures = []
        for image in group["images"]:
            warning = ", ".join(image["warnings"])
            if image["selected"]:
                prefix = f"{job['job_id']}/{group['series_id']}/{image['image_id']}"
                pictures.append(f'<figure><a href="{prefix}_preview.jpg"><img loading="lazy" src="{prefix}_thumbnail.webp"></a><img loading="lazy" src="{prefix}_primary.webp"><figcaption>#{image["rank"]} {html.escape(image["filename"])} — {image["score"]}/100 · {html.escape(warning)}<br>{image["bytes"]:,} → {image["upload_bytes"]:,} bytes</figcaption></figure>')
        listing = "; ".join(f'{html.escape(i["filename"])} ({i["score"]})' for i in group["images"])
        rows.append(f'<section><h2>Series {index} · {len(group["images"])} ảnh</h2><p>{listing}</p><div>{"".join(pictures)}</div></section>')
    document = '<!doctype html><meta charset="utf-8"><title>Kiểm tra series ảnh</title><style>body{font:15px system-ui;background:#101827;color:#eee;margin:30px}section{border-top:1px solid #456;padding:12px}section>div{display:flex;flex-wrap:wrap}figure{margin:8px;width:300px}img{width:145px;height:150px;object-fit:contain}p{overflow-wrap:anywhere;color:#abc}</style><h1>Kiểm tra bộ ảnh Downloads/1 — N = 3</h1><pre>'+html.escape(json.dumps(stats, ensure_ascii=False, indent=2))+'</pre>'+''.join(rows)
    (directory/"report.html").write_text(document, encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
