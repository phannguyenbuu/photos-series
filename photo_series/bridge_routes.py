"""Multipart adapter for the existing bridge orchestrator; no local inference."""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from flask import jsonify, request
from PIL import Image
try:
    from . import grid_batch
except ImportError:  # loaded as a top-level module in tests
    import grid_batch

GRID_PREFIX = "ps_"      # series ids answered through a shared grid request
GRID_QUIET = 5           # flush a job's pending series after this many seconds without a new upload
GRID_MAX_WAIT = 25       # ... or once the oldest pending series waited this long


class ProcessorUnavailable(RuntimeError):
    pass


# Photo-series context runs only on Gemini browser tabs (ChatGPT tabs hit attachment quotas).
PROVIDER = "gemini"
ENGINE = "gemini_browser"
# 5.6.4 opens a new conversation for jobs flagged fresh_conversation.
MIN_VERSION = (5, 6, 4)


def parse_result(text, metadata):
    try:
        start = text.find("{")
        if start < 0:
            return None
        parsed, _ = json.JSONDecoder().raw_decode(text[start:])
        expected = {image["image_id"] for image in metadata["images"]}
        if not isinstance(parsed.get("context"), str) or {i["image_id"] for i in parsed["images"]} != expected:
            return None
        return parsed
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def register_photo_series_bridge(app, orchestrator, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "series.sqlite3"
    lock = threading.Lock()
    @contextmanager
    def connect():
        con = sqlite3.connect(database, timeout=30)
        try:
            with con:
                yield con
        finally:
            con.close()

    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, digest TEXT, job_id TEXT, metadata TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS retries (previous_id TEXT PRIMARY KEY, next_id TEXT)")
        con.execute('CREATE TABLE IF NOT EXISTS cancelled_jobs (local_job_id TEXT PRIMARY KEY)')
        columns = {row[1] for row in con.execute("PRAGMA table_info(requests)")}
        con.execute("CREATE TABLE IF NOT EXISTS grid_batches (batch_id TEXT PRIMARY KEY, local_job_id TEXT, remote_job_id TEXT, members TEXT, created REAL)")
        if "layout" not in {row[1] for row in con.execute("PRAGMA table_info(grid_batches)")}:
            con.execute("ALTER TABLE grid_batches ADD COLUMN layout TEXT")  # tile rectangles, to map face boxes back
        for column, kind in (("payload", "TEXT"), ("result", "TEXT"), ("batch_id", "TEXT"), ("queued_at", "REAL")):
            if column not in columns:
                con.execute(f"ALTER TABLE requests ADD COLUMN {column} {kind}")

    def enqueue(payload):
        with orchestrator.lock:
            eligible = []
            for worker in orchestrator.get_all_workers():
                try:
                    version = tuple(int(part) for part in worker.get('bridge_version', '').split('.'))
                except ValueError:
                    version = ()
                if version >= MIN_VERSION and worker.get('is_online') and worker.get('is_enabled') and worker.get('can_receive_jobs') and worker.get('provider') == PROVIDER:
                    eligible.append(worker)
            requested = payload.get('target_tab_id')
            if requested:
                eligible = [worker for worker in eligible if worker['worker_id'] == requested]
            if not eligible:
                raise ProcessorUnavailable()
            def load(worker):
                return sum(job.get('target_worker_id') == worker['worker_id'] and job['status'] in ('queued', 'assigned', 'processing') for job in orchestrator.jobs)
            target = min(eligible, key=lambda worker: (load(worker), worker['worker_id']))['worker_id']
            job = orchestrator.create_job(job_type="chat_code", prompt=payload["prompt"],
                reference_images=payload["reference_images"], target_worker_id=target,
                engine=ENGINE, browser_provider=PROVIDER, save_to_gallery=False, new_chat=True)
            # The orchestrator forces new_chat off for Gemini tabs; this flag is read only by the photo flow.
            job["fresh_conversation"] = True
            return job

    @app.errorhandler(ProcessorUnavailable)
    def no_processor(_error):
        return jsonify(error='Chưa có bộ xử lý ngữ cảnh sẵn sàng. Vui lòng thử lại sau.'), 503

    def authorized():
        key = os.getenv("PHOTO_SERIES_API_KEY", "")
        return not key or hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + key)

    def cancel_record(con, remote_id, metadata, cached):
        if cached and json.loads(cached).get('status') == 'completed':
            return False
        if remote_id.startswith(GRID_PREFIX):
            result = dict(job_id=remote_id, series_id=metadata['series_id'], status='cancelled', result=None,
                          result_text='', structured_result_valid=False, cancelled=True)
            con.execute('UPDATE requests SET result=? WHERE job_id=?', (json.dumps(result), remote_id))
            batch = con.execute('SELECT batch_id FROM requests WHERE job_id=?', (remote_id,)).fetchone()
            if batch and batch[0]:
                open_members = con.execute('SELECT COUNT(*) FROM requests WHERE batch_id=? AND result IS NULL', (batch[0],)).fetchone()[0]
                remote = con.execute('SELECT remote_job_id FROM grid_batches WHERE batch_id=?', (batch[0],)).fetchone()
                if not open_members and remote:
                    with orchestrator.lock:
                        target = next((item for item in orchestrator.jobs if item['id'] == remote[0]), None)
                        if target and target['status'] in ('queued', 'assigned', 'processing'):
                            target['status'] = 'cancelled'
                            target['cancel_requested'] = True
                            target['current_step'] = 'Đã hủy bởi người dùng'
            return True
        with orchestrator.lock:
            target = next((item for item in orchestrator.jobs if item['id'] == remote_id), None)
            if target and target['status'] == 'completed':
                return False
            if target:
                target['status'] = 'cancelled'
                target['cancel_requested'] = True
                target['current_step'] = 'Đã hủy bởi người dùng'
        result = dict(job_id=remote_id, series_id=metadata['series_id'], status='cancelled', result=None,
                      result_text='', structured_result_valid=False, cancelled=True)
        con.execute('UPDATE requests SET result=? WHERE job_id=?', (json.dumps(result), remote_id))
        return True

    @app.route('/api/vision/jobs/<local_job_id>/cancel', methods=['POST'])
    def photo_job_cancel(local_job_id):
        if not authorized():
            return jsonify(error='Không được phép'), 401
        with lock, connect() as con:
            con.execute('INSERT OR IGNORE INTO cancelled_jobs VALUES (?)', (local_job_id,))
            count = 0
            for remote_id, metadata_text, cached in con.execute('SELECT job_id,metadata,result FROM requests').fetchall():
                metadata = json.loads(metadata_text)
                if metadata['job_id'] == local_job_id:
                    count += cancel_record(con, remote_id, metadata, cached)
        return jsonify(cancelled=True, cancelled_series=count)

    @app.route('/api/vision/jobs/<local_job_id>/resume', methods=['POST'])
    def photo_job_resume(local_job_id):
        if not authorized():
            return jsonify(error='Không được phép'), 401
        with lock, connect() as con:
            con.execute('DELETE FROM cancelled_jobs WHERE local_job_id=?', (local_job_id,))
        return jsonify(resumed=True)

    @app.route('/api/vision/series/<job_id>/cancel', methods=['POST'])
    def photo_series_cancel(job_id):
        if not authorized():
            return jsonify(error='Không được phép'), 401
        with lock, connect() as con:
            row = con.execute('SELECT metadata,result FROM requests WHERE job_id=?', (job_id,)).fetchone()
            if not row:
                return jsonify(error='Không tìm thấy tác vụ'), 404
            cancelled = cancel_record(con, job_id, json.loads(row[0]), row[1])
        return jsonify(cancelled=cancelled)

    @app.route("/api/vision/analyze", methods=["POST"])
    def photo_series_analyze():
        if not authorized():
            return jsonify(error="Không được phép"), 401
        if request.content_length is None or request.content_length > 32*1024*1024:
            return jsonify(error="Dữ liệu ảnh vượt giới hạn 32 MB"), 413
        try:
            metadata = json.loads(request.form["metadata"])
            for field in ("job_id", "series_id"):
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", metadata[field]):
                    raise ValueError()
            images = metadata["images"]
            if not isinstance(images, list) or len(images) != 1:
                raise ValueError()
            refs, order = [], []
            for image in images:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", image["image_id"]):
                    raise ValueError()
                for kind in ("thumbnail", "primary"):
                    content = request.files[image["fields"][kind]].read(4*1024*1024+1)
                    if len(content) > 4*1024*1024:
                        raise ValueError()
                    with Image.open(io.BytesIO(content)) as decoded:
                        if decoded.width*decoded.height > 4096*4096:
                            raise ValueError()
                        decoded.verify()
                        mime = Image.MIME[decoded.format]
                    refs.append("data:"+mime+";base64,"+base64.b64encode(content).decode())
                    order.append(dict(position=len(refs), image_id=image["image_id"], kind=kind))
            digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()+"".join(refs).encode()).hexdigest()
        except (KeyError, TypeError, ValueError, OSError):
            return jsonify(error="Metadata hoặc ảnh không hợp lệ"), 400
        key = metadata["job_id"]+":"+metadata["series_id"]
        with lock, connect() as con:
            if con.execute('SELECT 1 FROM cancelled_jobs WHERE local_job_id=?', (metadata['job_id'],)).fetchone():
                return jsonify(cancelled=True, error='Tác vụ đã hủy'), 409
            existing = con.execute("SELECT digest,job_id,result FROM requests WHERE key=?", (key,)).fetchone()
            if existing:
                if existing[0] != digest:
                    return jsonify(error="Series đã nhận có dữ liệu khác"), 409
                if existing[1].startswith(GRID_PREFIX) or orchestrator.get_job(existing[1]) or existing[2]:
                    return jsonify(job_id=existing[1]), 200
            thumbnail = next(ref for ref, item in zip(refs, order) if item["kind"] == "thumbnail")
            grid_id = GRID_PREFIX+secrets.token_hex(10)
            payload = dict(grid=True, image_id=images[0]["image_id"], thumbnail=thumbnail)
            con.execute("INSERT OR REPLACE INTO requests (key,digest,job_id,metadata,payload,result,batch_id,queued_at) VALUES (?,?,?,?,?,NULL,NULL,?)",
                        (key, digest, grid_id, json.dumps(metadata), json.dumps(payload), time.time()))
        return jsonify(job_id=grid_id), 202

    @app.route("/api/vision/series/<job_id>/retry", methods=["POST"])
    def photo_series_retry(job_id):
        if not authorized():
            return jsonify(error="Không được phép"), 401
        with lock, connect() as con:
            previous = con.execute("SELECT next_id FROM retries WHERE previous_id=?", (job_id,)).fetchone()
            if previous:
                return jsonify(job_id=previous[0])
            if job_id.startswith(GRID_PREFIX):
                row = con.execute("SELECT result FROM requests WHERE job_id=?", (job_id,)).fetchone()
                if not row:
                    return jsonify(error="Không tìm thấy tác vụ"), 404
                if not row[0] or json.loads(row[0]).get("status") not in ("failed", "cancelled"):
                    return jsonify(job_id=job_id)
                next_id = GRID_PREFIX+secrets.token_hex(10)
                con.execute("UPDATE requests SET job_id=?,result=NULL,batch_id=NULL,queued_at=? WHERE job_id=?", (next_id, time.time(), job_id))
                con.execute("INSERT INTO retries VALUES (?,?)", (job_id, next_id))
                return jsonify(job_id=next_id), 202
            row = con.execute("SELECT metadata,payload FROM requests WHERE job_id=?", (job_id,)).fetchone()
            old = orchestrator.get_job(job_id) if row else None
            if not row or (not old and not row[1]):
                return jsonify(error="Không tìm thấy tác vụ"), 404
            metadata = json.loads(row[0])
            invalid = old and old["status"] == "completed" and parse_result(old.get("result_text") or "", metadata) is None
            if old and old["status"] not in ('failed', 'cancelled') and not invalid:
                return jsonify(job_id=job_id)
            payload = json.loads(row[1]) if row[1] else dict(prompt=old["prompt"], reference_images=old["reference_images"], target_tab_id=metadata.get("target_tab_id"))
            payload["prompt"] += "\nChỉ JSON thuần gọn, tuyệt đối không thêm giải thích, mỗi trường văn bản tối đa 60 ký tự."
            job = enqueue(payload)
            con.execute("UPDATE requests SET job_id=?,payload=?,result=NULL WHERE job_id=?", (job["id"], json.dumps(payload), job_id))
            con.execute("INSERT INTO retries VALUES (?,?)", (job_id, job["id"]))
        return jsonify(job_id=job["id"]), 202

    @app.route("/api/vision/series/<job_id>")
    def photo_series_result(job_id):
        if not authorized():
            return jsonify(error="Không được phép"), 401
        with lock, connect() as con:
            row = con.execute("SELECT metadata,payload,result FROM requests WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return jsonify(error="Không tìm thấy tác vụ phân tích"), 404
            if row[2]:
                return jsonify(json.loads(row[2]))
            if job_id.startswith(GRID_PREFIX):
                return jsonify(grid_status(con, job_id))
            job = orchestrator.get_job(job_id)
            if not job and row[1]:
                job = enqueue(json.loads(row[1]))
                con.execute("UPDATE requests SET job_id=? WHERE job_id=?", (job["id"], job_id))
                job_id = job["id"]
            if not job:
                return jsonify(error="Không tìm thấy tác vụ phân tích"), 404
        metadata = json.loads(row[0])
        text = job.get("result_text") or ""
        parsed = None
        if job["status"] == "completed":
            parsed = parse_result(text, metadata)
        status = "failed" if job["status"] == "completed" and parsed is None else job["status"]
        response = dict(job_id=job_id, series_id=metadata["series_id"], status=status,
                        result=parsed, result_text=text, structured_result_valid=parsed is not None)
        if status in ("completed", "failed", 'cancelled'):
            with connect() as con:
                con.execute("UPDATE requests SET result=? WHERE job_id=?", (json.dumps(response), job_id))
        return jsonify(response)

    def grid_settle(con, batch_id):
        """Write results for every open member once the batch's request has finished."""
        remote = con.execute("SELECT remote_job_id,members,layout FROM grid_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if not remote:
            return
        job = orchestrator.get_job(remote[0])
        members = json.loads(remote[1])
        if not job:
            # Bridge restarted and the request was lost: queue the open members again.
            con.execute("UPDATE requests SET batch_id=NULL WHERE batch_id=? AND result IS NULL", (batch_id,))
            return
        if job["status"] not in ("completed", "failed", "cancelled"):
            return
        text = job.get("result_text") or ""
        layout = json.loads(remote[2]) if remote[2] else None
        found = grid_batch.split(text, [image_id for _, image_id in members], layout) if job["status"] == "completed" else {}
        for grid_id, image_id in members:
            row = con.execute("SELECT metadata,result FROM requests WHERE job_id=?", (grid_id,)).fetchone()
            if not row or row[1]:
                continue
            metadata = json.loads(row[0])
            parsed = found.get(image_id)
            response = dict(job_id=grid_id, series_id=metadata["series_id"], status="completed" if parsed else "failed",
                            result=parsed, result_text=json.dumps(parsed, ensure_ascii=False) if parsed else text[:2000],
                            structured_result_valid=parsed is not None, grid_cells=len(members))
            con.execute("UPDATE requests SET result=? WHERE job_id=?", (json.dumps(response), grid_id))

    def grid_status(con, grid_id):
        row = con.execute("SELECT metadata,batch_id FROM requests WHERE job_id=?", (grid_id,)).fetchone()
        metadata = json.loads(row[0])
        status = "queued"
        if row[1]:
            grid_settle(con, row[1])
            done = con.execute("SELECT result FROM requests WHERE job_id=?", (grid_id,)).fetchone()
            if done and done[0]:
                return json.loads(done[0])
            remote = con.execute("SELECT remote_job_id FROM grid_batches WHERE batch_id=?", (row[1],)).fetchone()
            job = orchestrator.get_job(remote[0]) if remote else None
            status = job["status"] if job else "queued"
        return dict(job_id=grid_id, series_id=metadata["series_id"], status=status, result=None, result_text="", structured_result_valid=False)

    def grid_flush():
        now = time.time()
        with lock, connect() as con:
            for (batch_id,) in con.execute("SELECT DISTINCT batch_id FROM requests WHERE batch_id IS NOT NULL AND result IS NULL").fetchall():
                grid_settle(con, batch_id)
            pending = {}
            for grid_id, metadata_text, payload_text, queued_at in con.execute(
                    "SELECT job_id,metadata,payload,queued_at FROM requests WHERE job_id LIKE ? AND result IS NULL AND batch_id IS NULL ORDER BY queued_at",
                    (GRID_PREFIX+"%",)).fetchall():
                local_job = json.loads(metadata_text)["job_id"]
                pending.setdefault(local_job, []).append((grid_id, json.loads(payload_text), float(queued_at or now)))
            for local_job, rows in pending.items():
                if con.execute("SELECT 1 FROM cancelled_jobs WHERE local_job_id=?", (local_job,)).fetchone():
                    continue
                newest, oldest = max(r[2] for r in rows), min(r[2] for r in rows)
                if len(rows) < grid_batch.MAX_CELLS and now-newest < GRID_QUIET and now-oldest < GRID_MAX_WAIT:
                    continue
                chunk = rows[:grid_batch.MAX_CELLS]
                thumbnails = [base64.b64decode(payload["thumbnail"].split(",", 1)[1]) for _, payload, _ in chunk]
                image_ids = [payload["image_id"] for _, payload, _ in chunk]
                reference, n, layout = grid_batch.compose_with_layout(thumbnails)
                try:
                    job = enqueue(dict(prompt=grid_batch.prompt(image_ids, n), reference_images=[reference]))
                except ProcessorUnavailable:
                    return
                batch_id = "grid_"+secrets.token_hex(8)
                members = [[grid_id, payload["image_id"]] for grid_id, payload, _ in chunk]
                con.execute("INSERT INTO grid_batches (batch_id,local_job_id,remote_job_id,members,created,layout) VALUES (?,?,?,?,?,?)",
                            (batch_id, local_job, job["id"], json.dumps(members), now, json.dumps(layout)))
                con.executemany("UPDATE requests SET batch_id=? WHERE job_id=?", [(batch_id, grid_id) for grid_id, _ in members])

    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS split_requests (split_id TEXT PRIMARY KEY, request_key TEXT UNIQUE, clusters TEXT, payload TEXT, remote_job_id TEXT, result TEXT)")

    @app.route("/api/vision/split", methods=["POST"])
    def photo_people_split():
        """Partition local groups by the set of people: one grid of segment thumbnails, several groups per grid."""
        if not authorized():
            return jsonify(error="Không được phép"), 401
        if request.content_length is None or request.content_length > 32*1024*1024:
            return jsonify(error="Dữ liệu ảnh vượt giới hạn 32 MB"), 413
        try:
            metadata = json.loads(request.form["metadata"])
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", metadata["job_id"]):
                raise ValueError()
            clusters = metadata["clusters"]
            ids = [image_id for cluster in clusters for image_id in cluster]
            if not clusters or any(len(c) < 2 for c in clusters) or len(ids) > grid_batch.SPLIT_MAX_TILES or len(set(ids)) != len(ids):
                raise ValueError()
            thumbnails = []
            for image_id in ids:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", image_id):
                    raise ValueError()
                content = request.files[image_id].read(4*1024*1024+1)
                if len(content) > 4*1024*1024:
                    raise ValueError()
                with Image.open(io.BytesIO(content)) as decoded:
                    if decoded.width*decoded.height > 2048*2048:
                        raise ValueError()
                    decoded.verify()
                thumbnails.append(content)
        except (KeyError, TypeError, ValueError, OSError):
            return jsonify(error="Metadata hoặc ảnh không hợp lệ"), 400
        # `attempt` lets the agent ask again after a refused or unusable answer without hitting the idempotent copy.
        key = metadata["job_id"]+":"+str(metadata.get("attempt", 0))+":"+hashlib.sha256(json.dumps(clusters).encode()+b"".join(thumbnails)).hexdigest()
        with lock, connect() as con:
            if con.execute('SELECT 1 FROM cancelled_jobs WHERE local_job_id=?', (metadata['job_id'],)).fetchone():
                return jsonify(cancelled=True, error='Tác vụ đã hủy'), 409
            existing = con.execute("SELECT split_id FROM split_requests WHERE request_key=?", (key,)).fetchone()
            if existing:
                return jsonify(split_id=existing[0]), 200
            reference, n = grid_batch.compose(thumbnails)
            payload = dict(prompt=grid_batch.people_prompt([len(c) for c in clusters], n), reference_images=[reference])
            try:
                remote_id = enqueue(payload)["id"]
            except ProcessorUnavailable:
                remote_id = None  # the tab is busy right now; queued again when the agent polls
            split_id = "split_"+secrets.token_hex(10)
            con.execute("INSERT INTO split_requests VALUES (?,?,?,?,?,NULL)", (split_id, key, json.dumps(clusters), json.dumps(payload), remote_id))
        return jsonify(split_id=split_id), 202

    @app.route("/api/vision/split/<split_id>")
    def photo_people_split_result(split_id):
        if not authorized():
            return jsonify(error="Không được phép"), 401
        with lock, connect() as con:
            row = con.execute("SELECT clusters,payload,remote_job_id,result FROM split_requests WHERE split_id=?", (split_id,)).fetchone()
            if not row:
                return jsonify(error="Không tìm thấy yêu cầu"), 404
            if row[3]:
                return jsonify(json.loads(row[3]))
            clusters = json.loads(row[0])
            job = orchestrator.get_job(row[2]) if row[2] else None
            if not job:
                try:
                    job = enqueue(json.loads(row[1]))
                except ProcessorUnavailable:
                    return jsonify(split_id=split_id, status="queued")
                con.execute("UPDATE split_requests SET remote_job_id=? WHERE split_id=?", (job["id"], split_id))
            if job["status"] not in ("completed", "failed", "cancelled"):
                return jsonify(split_id=split_id, status=job["status"])
            parts = grid_batch.people_split(job.get("result_text") or "", [len(c) for c in clusters]) if job["status"] == "completed" else [None]*len(clusters)
            groups = [None if p is None else [[cluster[i] for i in part] for part in p] for cluster, p in zip(clusters, parts)]
            usable = any(g is not None for g in groups)
            response = dict(split_id=split_id, status="completed" if usable else "failed", groups=groups)
            con.execute("UPDATE split_requests SET result=? WHERE split_id=?", (json.dumps(response), split_id))
        return jsonify(response)

    def split_flush():
        with lock, connect() as con:
            for split_id, payload in con.execute("SELECT split_id,payload FROM split_requests WHERE remote_job_id IS NULL AND result IS NULL").fetchall():
                try:
                    job = enqueue(json.loads(payload))
                except ProcessorUnavailable:
                    return
                con.execute("UPDATE split_requests SET remote_job_id=? WHERE split_id=?", (job["id"], split_id))

    stop = threading.Event()
    app.extensions["photo_series_stop"] = stop  # lets tests end the loop before removing its folder

    def grid_loop():
        while directory.exists() and not stop.is_set():
            try:
                split_flush()
                grid_flush()
            except Exception:
                app.logger.exception("Photo grid batching failed")
            stop.wait(1)

    threading.Thread(target=grid_loop, name="photo-grid-batcher", daemon=True).start()
