"""Register on the existing PrintAgent Flask app (loopback only)."""
import json
import os
import threading
import uuid
import time
from pathlib import Path
from flask import jsonify, request, send_file
from .pipeline import Pipeline, configuration

ORIGINS = {
    "https://dir.toolxprint.com",
    "https://toolxprint.com",
    "https://www.toolxprint.com",
    "https://mzpic.com",
    "https://app.mzpic.com",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}


def configured_origins(settings_path):
    """Extra web origins allowed on this machine: `web.allowed_origins` (or top-level `allowed_origins`) in settings.json.

    Each entry must be an exact origin such as "https://shop.example.com" (scheme, host and optional port, no path).
    """
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    values = ((data.get("web") or {}).get("allowed_origins") if isinstance(data.get("web"), dict) else None) or data.get("allowed_origins") or []
    return {str(value).strip().rstrip("/").lower() for value in values if isinstance(value, str) and value.strip() and value.strip() != "*"}


def register_photo_series_routes(app, directory=None):
    storage = Path(directory or Path(os.getenv("APPDATA", str(Path.home()))) / "GoxPrintAgent" / "photo-series")
    pipeline = Pipeline(storage)
    running = set()
    lock = threading.Lock()
    picker_lock = threading.Lock()
    settings_path = storage.parent/"settings.json"

    def origin_allowed(origin):
        return origin is None or origin in ORIGINS or origin.rstrip("/").lower() in configured_origins(settings_path)

    @app.route('/api/photo-series/health')
    def photo_series_health():
        return jsonify(ready=True, version=3, raw_supported=True, analysis_images_per_series=1,
                       cancel_supported=True, grouping_version=3)

    @app.route('/api/photo-series/pick-folder', methods=['POST', 'OPTIONS'])
    def photo_series_pick_folder():
        if not picker_lock.acquire(blocking=False):
            return jsonify(error='Hộp chọn thư mục đang mở'), 409
        root = None
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            folder = filedialog.askdirectory(parent=root, title='Chọn thư mục ảnh', mustexist=True)
            return jsonify(folder=str(Path(folder).resolve()) if folder else '', cancelled=not bool(folder))
        except Exception:
            return jsonify(error='Không mở được hộp chọn thư mục trên máy này'), 500
        finally:
            if root is not None:
                root.destroy()
            picker_lock.release()

    @app.before_request
    def guard_photo_series():
        if not request.path.startswith("/api/photo-series"):
            return
        if request.remote_addr not in ("127.0.0.1", "::1") or not origin_allowed(request.headers.get("Origin")):
            return jsonify(error="Origin không được phép"), 403
        if request.method == "OPTIONS":
            return "", 204
        if request.method == "POST" and request.headers.get("X-Photo-Series") != "1":
            return jsonify(error="Thiếu header xác thực local"), 403

    @app.after_request
    def cors_photo_series(response):
        if request.path.startswith("/api/photo-series") and request.headers.get("Origin") and origin_allowed(request.headers.get("Origin")):
            response.headers["Access-Control-Allow-Origin"] = request.headers["Origin"]
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Photo-Series"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers.add("Vary", "Origin")
        return response

    def start(job, cfg):
        with lock:
            if running:
                return False
            running.add(job["job_id"])
        def work():
            try:
                pipeline.run(job, cfg)
            finally:
                with lock:
                    running.discard(job["job_id"])
        threading.Thread(target=work, daemon=True).start()
        return True

    @app.route("/api/photo-series/jobs", methods=["POST", "OPTIONS"])
    def photo_series_create():
        try:
            data = request.get_json()
            folder = Path(data["folder"]).resolve(strict=True)
            if not folder.is_dir():
                raise ValueError("Cần chọn thư mục ảnh")
            defaults = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
            user_config = data.get('config', {})
            # UI controls selection count only; operational secrets stay on the agent.
            cfg = configuration(defaults | {'n': user_config.get('n', defaults['n']), 'grouping_mode': 'auto'})
        except (ValueError, KeyError, TypeError, OSError):
            return jsonify(error="Thư mục hoặc cấu hình không hợp lệ"), 400
        job_id = uuid.uuid4().hex
        job = dict(job_id=job_id, folder=str(folder), status="pending", series=[], processed=0, total=0,
                   created_at=time.time(), elapsed_seconds=0)
        # Keys stay in local configuration and never appear in UI responses.
        (storage / (job_id+".json")).write_text(json.dumps(cfg), encoding="utf-8")
        pipeline.save(job)
        if not start(job, cfg):
            return jsonify(error="Đang có tác vụ xử lý ảnh"), 409
        return jsonify(job_id=job_id), 202

    @app.route("/api/photo-series/jobs/<job_id>", methods=["GET"])
    def photo_series_status(job_id):
        job = pipeline.get(job_id)
        if not job:
            return jsonify(error="Không tìm thấy tác vụ"), 404
        # Polling does not change durable state; stopped runs can be resumed explicitly.
        with lock:
            job["running"] = job_id in running
        job['elapsed_seconds'] = pipeline.elapsed(job)
        if not job.get('started_at') and (storage/(job_id+'.json')).exists():
            config_stat = (storage/(job_id+'.json')).stat()
            job['started_at'] = getattr(config_stat, 'st_birthtime', config_stat.st_mtime)
            job['elapsed_seconds'] = max(0, (time.time() if job['running'] else job['updated_at'])-job['started_at'])
        return jsonify(job)

    exports = {}

    @app.route('/api/photo-series/jobs/<job_id>/export', methods=['POST', 'GET', 'OPTIONS'])
    def photo_series_export(job_id):
        """POST {count: 1|2|3} copies the best photos of each series into a new `selected` subfolder; GET reports progress."""
        if request.method == 'GET':
            state = exports.get(job_id)
            return (jsonify(state), 200) if state else (jsonify(error='Chưa lọc ảnh cho tác vụ này'), 404)
        job = pipeline.get(job_id)
        if not job:
            return jsonify(error='Không tìm thấy tác vụ'), 404
        count = (request.get_json(silent=True) or {}).get('count')
        if count not in (1, 2, 3):
            return jsonify(error='Chọn 1, 2 hoặc 3 ảnh'), 400
        with lock:
            if job_id in running:
                return jsonify(error='Tác vụ đang xử lý; hãy chờ xong rồi lọc ảnh'), 409
            if exports.get(job_id, {}).get('status') == 'running':
                return jsonify(error='Đang lọc ảnh'), 409
            state = exports[job_id] = dict(status='running', count=count, done=0, total=0)
        def work():
            from .export import export_best
            def progress(done, total):
                state.update(done=done, total=total)
            try:
                state.update(status='completed', **export_best(job, count, progress))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Photo export failed: %s', job_id)
                state.update(status='failed', error='Không lọc được ảnh. Kiểm tra quyền ghi thư mục.')
        threading.Thread(target=work, daemon=True).start()
        return jsonify(state), 202

    @app.route('/api/photo-series/jobs/<job_id>/cancel', methods=['POST', 'OPTIONS'])
    def photo_series_cancel(job_id):
        job = pipeline.request_cancel(job_id)
        if not job:
            return jsonify(error='Không tìm thấy tác vụ'), 404
        config_path = storage/(job_id+'.json')
        if job['status'] == 'cancelled' and config_path.exists():
            cfg = configuration(json.loads(config_path.read_text(encoding='utf-8')))
            job['remote_cancel_pending'] = True
            pipeline.save(job)
            threading.Thread(target=pipeline.cancel_remote_pending, args=(job, cfg), daemon=True).start()
        return jsonify(job_id=job_id, status=job['status'], cancel_requested=job.get('cancel_requested', False)), 202

    @app.route("/api/photo-series/jobs/<job_id>/resume", methods=["POST", "OPTIONS"])
    def photo_series_resume(job_id):
        job = pipeline.get(job_id)
        if not job:
            return jsonify(error="Không tìm thấy tác vụ"), 404
        if job["status"] == "completed":
            return jsonify(job_id=job_id), 200
        cfg = configuration(json.loads((storage/(job_id+".json")).read_text(encoding="utf-8")))
        if job.get('remote_cancel_pending'):
            return jsonify(error='Đang hoàn tất yêu cầu hủy trên máy chủ'), 409
        if job.get('cancel_requested'):
            import requests
            try:
                response = requests.post(cfg['server_url']+'/api/vision/jobs/'+job_id+'/resume',
                                         headers=pipeline.headers(cfg), timeout=min(cfg['timeout'], 8))
                response.raise_for_status()
            except requests.RequestException:
                return jsonify(error='Chưa kết nối được máy chủ để tiếp tục tác vụ đã hủy'), 502
            pipeline.clear_cancel(job)
        for group in job.get('series', []):
            if group['status'] != 'completed':
                group['analysis_attempts'] = 0
        if not start(job, cfg):
            return jsonify(error="Đang có tác vụ xử lý ảnh"), 409
        return jsonify(job_id=job_id), 202

    @app.route("/api/photo-series/jobs/<job_id>/images/<image_id>/<kind>")
    def photo_series_image(job_id, image_id, kind):
        job = pipeline.get(job_id)
        if job and kind in ("thumbnail", "preview", "primary"):
            for group in job["series"]:
                image = next((item for item in group["images"] if item["image_id"] == image_id), None)
                if not image:
                    continue
                suffix = ".jpg" if kind == "preview" else ".webp"
                path = storage/job_id/group["series_id"]/(image_id+"_"+kind+suffix)
                if not path.is_file() and kind != "primary":
                    # Photos that were not selected have no prepared thumbnail; make one on first view.
                    try:
                        from .image_source import read_image
                        rgb, _ = read_image(Path(image["path"]), 800 if kind == "preview" else 480)
                        rgb.thumbnail((800, 800) if kind == "preview" else (480, 480))
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_name(path.stem+".tmp"+suffix)
                        rgb.save(temporary, "JPEG" if kind == "preview" else "WEBP", quality=85)
                        temporary.replace(path)
                    except Exception:
                        return jsonify(error="Không đọc được ảnh"), 404
                if path.is_file():
                    return send_file(path)
        return jsonify(error="Không tìm thấy ảnh"), 404
