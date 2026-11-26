"""CPU-only series selection. Originals are never modified."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageOps, ImageDraw
from .image_source import read_image, RAW_EXTENSIONS
from .grouping import extract_features, group_scenes
from .export import is_export_dir

DEFAULTS = dict(n=3, series_gap_seconds=8, grouping_mode='auto', similarity_threshold=0.80,
                analysis_dimension=640, crop_dimension=1280, thumbnail_dimension=480,
                ellipse_ratio=2/3, quality=85, timeout=60, max_retries=3,
                server_url="https://genai.toolxprint.com", api_key="",
                target_tab_id="", poll_seconds=3, concurrent_uploads=32, analysis_retries=2)
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"} | RAW_EXTENSIONS


class JobCancelled(Exception):
    pass


def configuration(values):
    cfg = DEFAULTS | values
    for key, low, high in [("n", 1, 20), ("analysis_dimension", 64, 2048),
                           ("crop_dimension", 64, 4096), ("thumbnail_dimension", 64, 1024),
                           ("quality", 1, 100), ("max_retries", 0, 8), ("concurrent_uploads", 1, 32), ('analysis_retries', 0, 3)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f"Giá trị {key} không hợp lệ")
    for key, low, high in [("series_gap_seconds", 0, 300), ("similarity_threshold", 0, 1),
                           ("ellipse_ratio", .1, 1), ("timeout", 1, 300), ("poll_seconds", 1, 30)]:
        if not isinstance(cfg[key], (float, int)) or not math.isfinite(cfg[key]) or not low <= cfg[key] <= high:
            raise ValueError(f"Giá trị {key} không hợp lệ")
    if cfg["server_url"].rstrip("/") != DEFAULTS["server_url"]:
        raise ValueError("Máy chủ phân tích không được phép")
    return cfg


class Pipeline:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "state.sqlite3"
        self.state_lock = threading.RLock()
        self.cancel_events = {}
        self.active_jobs = {}
        self.timings = {}
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS metrics (hash TEXT, version TEXT, data TEXT, PRIMARY KEY(hash,version))")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        try:
            with con:
                yield con
        finally:
            con.close()

    def save(self, job):
        with self.state_lock, self.connect() as con:
            if job['job_id'] in self.timings:
                started, previous = self.timings[job['job_id']]
                job['elapsed_seconds'] = round(previous+time.monotonic()-started, 3)
            job["updated_at"] = time.time()
            con.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["job_id"], json.dumps(job, ensure_ascii=False)))

    def get(self, job_id):
        with self.connect() as con:
            row = con.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def cancel_marker(self, job_id):
        return self.directory/(job_id+'.cancel')

    def check_cancel(self, job):
        event = self.cancel_events.get(job['job_id'])
        if (event and event.is_set()) or self.cancel_marker(job['job_id']).exists():
            raise JobCancelled()

    def interruptible_wait(self, job, seconds):
        event = self.cancel_events.setdefault(job['job_id'], threading.Event())
        if event.wait(seconds):
            raise JobCancelled()
        self.check_cancel(job)

    def clear_cancel(self, job):
        self.cancel_events.setdefault(job['job_id'], threading.Event()).clear()
        self.cancel_marker(job['job_id']).unlink(missing_ok=True)
        job['cancel_requested'] = False
        job.pop('error', None)

    def request_cancel(self, job_id):
        event = self.cancel_events.setdefault(job_id, threading.Event())
        with self.state_lock:
            job = self.active_jobs.get(job_id) or self.get(job_id)
            if not job:
                return None
            if job['status'] in ('completed', 'cancelled') and job_id not in self.active_jobs:
                return job
            event.set()
            self.cancel_marker(job_id).write_text('cancel', encoding='ascii')
            job['cancel_requested'] = True
            job['status'] = 'cancelling' if job_id in self.active_jobs else 'cancelled'
            self.save(job)
            return job

    def cancel_remote_group(self, group, cfg):
        if not group.get('remote_job_id') or group.get('status') == 'completed':
            return
        try:
            response = requests.post(cfg['server_url']+'/api/vision/series/'+group['remote_job_id']+'/cancel',
                                     headers=self.headers(cfg), timeout=min(cfg['timeout'], 8))
            response.raise_for_status()
            group['remote_cancel_acknowledged'] = response.json().get('cancelled', False)
        except (requests.RequestException, ValueError):
            group['remote_cancel_acknowledged'] = False

    def cancel_remote_pending(self, job, cfg):
        job['remote_cancel_pending'] = True
        try:
            response = requests.post(cfg['server_url']+'/api/vision/jobs/'+job['job_id']+'/cancel',
                                     headers=self.headers(cfg), timeout=min(cfg['timeout'], 8))
            response.raise_for_status()
            job['remote_cancel_acknowledged'] = response.json().get('cancelled', False)
        except (requests.RequestException, ValueError):
            job['remote_cancel_acknowledged'] = False
        job['remote_cancel_pending'] = False
        self.save(job)

    def elapsed(self, job):
        if job['job_id'] in self.timings:
            started, previous = self.timings[job['job_id']]
            return round(previous+time.monotonic()-started, 3)
        return job.get('elapsed_seconds', 0)

    def inspect(self, path, cfg):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        version = f"v5:{cfg['analysis_dimension']}"
        with self.connect() as con:
            row = con.execute("SELECT data FROM metrics WHERE hash=? AND version=?", (digest, version)).fetchone()
        if row:
            result = json.loads(row[0])
        else:
            image, source_metadata = read_image(path, cfg['analysis_dimension'])
            width, height = source_metadata['original_width'], source_metadata['original_height']
            captured = source_metadata['captured_at']
            image.thumbnail((cfg["analysis_dimension"],) * 2)
            rgb = np.asarray(image)
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
            hist = (hist / max(float(hist.sum()), 1)).flatten()
            # Brightness-independent structure also prevents matching different scenes of similar colors.
            structure = cv2.resize(gray, (16, 16)).astype(float)
            structure = (structure - structure.mean()) / max(float(structure.std()), 1)
            lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
            result = dict(hash=digest, original_width=width, original_height=height,
                          captured_at=captured, time_source="exif" if captured is not None else "mtime",
                          sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                          dark=float((gray < 12).mean()), clipped=float((gray > 247).mean()),
                          brightness=float(gray.mean()/255), color=lab[:, :, 1:].mean(axis=(0, 1)).tolist(),
                          histogram=hist.tolist(), structure=structure.flatten().tolist())
            result['decode_method'] = source_metadata['decode_method']
            result['features'] = extract_features(gray)
            for field in ('capture_time_original', 'subsecond_original', 'utc_offset', 'camera_signature'):
                result[field] = source_metadata.get(field)
            with self.connect() as con:
                con.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?)", (digest, version, json.dumps(result)))
        result |= dict(path=str(path), filename=path.name, bytes=path.stat().st_size,
                       image_id=hashlib.sha256((str(path)+digest).encode()).hexdigest()[:24])
        if result["captured_at"] is None:
            result["sort_time"] = path.stat().st_mtime
        else:
            result["sort_time"] = result["captured_at"]
        return result

    @staticmethod
    def similarity(a, b):
        color = float(np.minimum(a["histogram"], b["histogram"]).sum())
        structure = float(np.clip(np.dot(a["structure"], b["structure"])/256, 0, 1))
        return .55*color + .45*structure

    def run(self, job, cfg):
        with self.state_lock:
            self.active_jobs[job['job_id']] = job
            self.cancel_events.setdefault(job['job_id'], threading.Event())
            self.timings[job['job_id']] = (time.monotonic(), job.get('elapsed_seconds', 0))
            job.setdefault('started_at', time.time())
            job.pop('finished_at', None)
        try:
            self.check_cancel(job)
            if not job.get("series"):
                self.scan(job, cfg)
            self.split_by_people(job, cfg)
            # The bridge packs 16 waiting series of one job into one grid request. Keeping 32 in flight lets the
            # next 16 be prepared and queued while the current grid is still being answered.
            with ThreadPoolExecutor(max_workers=cfg["concurrent_uploads"]) as pool:
                futures = [pool.submit(self.run_group, job, group, cfg) for group in job["series"] if group.get("status") != "completed"]
                failures = []
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as error:
                        failures.append(error)
                self.check_cancel(job)
                if failures:
                    raise failures[0]
            job["status"] = "completed"
            job.pop("error", None)
        except JobCancelled:
            job['status'] = 'cancelled'
            job['cancel_requested'] = True
            job.pop('error', None)
            for group in job.get('series', []):
                if group['status'] != 'completed':
                    group['status'] = 'cancelled'
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Photo series job failed: %s", job["job_id"])
            job["status"] = "failed"
            job["error"] = "Tác vụ chưa hoàn tất. Kiểm tra nhật ký và kết nối rồi bấm tiếp tục."
        finally:
            if job.get('status') == 'cancelled':
                self.cancel_remote_pending(job, cfg)
            with self.state_lock:
                job['finished_at'] = time.time()
                self.save(job)
                self.timings.pop(job['job_id'], None)
                self.active_jobs.pop(job['job_id'], None)

    def split_by_people(self, job, cfg):
        """Once per job, before any context request: split groups that mix different people at the same spot."""
        if job.get("people_split_done"):
            return
        if any(group.get("remote_job_id") or group.get("status") == "completed" for group in job["series"]):
            job["people_split_done"] = True  # older jobs already analysed: never discard their contexts
            return
        from .people_split import split_people
        with self.state_lock:
            job["status"] = "splitting"
            self.save(job)
        try:
            job["people_split"] = split_people(self, job, cfg)
        except JobCancelled:
            raise
        except Exception:
            import logging
            logging.getLogger(__name__).exception("People split skipped: %s", job["job_id"])
            job["people_split"] = dict(skipped=True)
        with self.state_lock:
            job["people_split_done"] = True
            job["status"] = "selected"
            self.save(job)

    def run_group(self, job, group, cfg):
        try:
            self.check_cancel(job)
            self._run_group(job, group, cfg)
        except JobCancelled:
            self.cancel_remote_group(group, cfg)
            raise

    def _run_group(self, job, group, cfg):
        if group.get("status") in ('failed', 'cancelled') and group.get("remote_job_id"):
            response = requests.post(cfg["server_url"] + "/api/vision/series/" + group["remote_job_id"] + "/retry",
                                     headers=self.headers(cfg), timeout=cfg["timeout"])
            with self.state_lock:
                if response.status_code == 404:
                    group.pop("remote_job_id", None)
                else:
                    response.raise_for_status()
                    group["remote_job_id"] = response.json()["job_id"]
                    group["status"] = "queued"
                self.save(job)
        if not group.get("remote_job_id"):
            with self.state_lock:
                self.check_cancel(job)
                job["status"] = "preparing"
                group["status"] = "preparing"
                self.save(job)
                metadata, files = self.prepare(job, group, cfg)
                job["status"] = "uploading"
                group["status"] = "uploading"
                self.save(job)
            self.check_cancel(job)
            response = self.upload(metadata, files, cfg)
            with self.state_lock:
                group["remote_job_id"] = response["job_id"]
                group["status"] = "queued"
                self.save(job)
            self.check_cancel(job)
        with self.state_lock:
            job["status"] = "analyzing"
            self.save(job)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self.check_cancel(job)
            try:
                response = requests.get(cfg["server_url"] + "/api/vision/series/" + group["remote_job_id"],
                                        headers=self.headers(cfg), timeout=cfg["timeout"])
                response.raise_for_status()
                data = response.json()
            except (requests.ConnectionError, requests.Timeout):
                self.interruptible_wait(job, cfg['poll_seconds'])
                continue
            with self.state_lock:
                self.check_cancel(job)
                group["status"] = data["status"]
                group["remote_job_id"] = data.get("job_id", group["remote_job_id"])
                if data["status"] == "completed":
                    group["result"] = data.get("result")
                    group["result_text"] = data.get("result_text", "")
                    self.save(job)
                    return
                self.save(job)
            if data["status"] == "failed":
                if group.get('analysis_attempts', 0) < cfg['analysis_retries']:
                    with self.state_lock:
                        group['analysis_attempts'] = group.get('analysis_attempts', 0)+1
                        self.save(job)
                    self.check_cancel(job)
                    return self._run_group(job, group, cfg)
                raise RuntimeError("Phân tích series thất bại trên máy chủ")
            if data['status'] == 'cancelled':
                self.cancel_events[job['job_id']].set()
                raise JobCancelled()
            self.interruptible_wait(job, cfg['poll_seconds'])
        raise TimeoutError("Chưa có kết quả; bấm tiếp tục để kiểm tra lại")

    def scan(self, job, cfg):
        root = Path(job["folder"]).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Thư mục ảnh không hợp lệ")
        # Skip copies made by "Lọc tất cả ảnh" (selected, selected_2, ...) so they are never re-grouped as new captures.
        paths = sorted(p for p in root.rglob("*") if p.suffix.lower() in EXTENSIONS and p.is_file()
                       and not p.is_relative_to(self.directory.resolve())
                       and not any(is_export_dir(part) for part in p.relative_to(root).parts[:-1]))
        # JPEG+RAW with the same stem are one camera capture, not two candidates.
        captures = {}
        for path in paths:
            captures.setdefault(str(path.with_suffix('')).casefold(), []).append(path)
        job |= dict(status="scanning", total=len(captures), total_files=len(paths), processed=0, errors=[], series=[],
                    raw_files=sum(p.suffix.lower() in RAW_EXTENSIONS for p in paths), raw_files_skipped=0,
                    pipeline_version=3, analysis_images_per_series=1)
        self.save(job)
        images = []
        for variants in captures.values():
            self.check_cancel(job)
            variants.sort(key=lambda p: (p.suffix.lower() in RAW_EXTENSIONS, p.suffix.lower() not in ('.jpg', '.jpeg'), str(p)))
            try:
                failures = []
                for path in variants:
                    try:
                        image = self.inspect(path, cfg)
                        image['source_files'] = [str(p) for p in variants]
                        image['formats'] = [p.suffix.lstrip('.').upper() for p in variants]
                        images.append(image)
                        break
                    except Exception:
                        failures.append(str(path))
                else:
                    raise ValueError('No readable capture variant')
            except Exception:
                job["errors"].append(dict(filename=str(variants[0]), error="Không đọc được ảnh", files=failures))
            job["processed"] += 1
            self.save(job)
        images.sort(key=lambda image: (image["sort_time"], image["path"]))
        groups, job['grouping'] = group_scenes(images, cfg, lambda: self.check_cancel(job))
        for group in groups:
            colors = np.array([image["color"] for image in group])
            median = np.median(colors, axis=0)
            deviations = np.linalg.norm(colors-median, axis=1)
            threshold = max(8., float(np.median(deviations) + 3*np.median(abs(deviations-np.median(deviations)))))
            sharp = sorted(image["sharpness"] for image in group)
            for image, deviation in zip(group, deviations):
                image["color_deviation"] = float(deviation)
                image["warnings"] = []
                if len(group) >= 3 and deviation > threshold:
                    image["warnings"].append("suspected_color_outlier")
                if image["time_source"] != "exif":
                    image["warnings"].append("missing_capture_time")
                if image["dark"] > .3:
                    image["warnings"].append("underexposure")
                if image["clipped"] > .15:
                    image["warnings"].append("highlight_clipping")
                # Rank sharpness within the same scene, avoiding arbitrary absolute cutoffs.
                rank = sum(value <= image["sharpness"] for value in sharp)/len(sharp)
                image["score"] = round(100*(.6*rank + .3*(1-min(1., image["dark"]+image["clipped"]))
                    + .1*(1-min(1., float(deviation)/32))), 2)
                image.pop("histogram")
                image.pop("structure")
                image.pop('features', None)
            group.sort(key=lambda image: (-image["score"], image["image_id"]))
            for rank, image in enumerate(group, 1):
                image |= dict(rank=rank, selected=rank <= cfg["n"], analysis_representative=rank == 1)
            series_id = hashlib.sha256("|".join(sorted(i["image_id"] for i in group)).encode()).hexdigest()[:24]
            first, last = min(group, key=lambda i: i['sort_time']), max(group, key=lambda i: i['sort_time'])
            job["series"].append(dict(series_id=series_id, status="selected", images=group, representative_image_id=group[0]['image_id'],
                capture_time_start=first.get('capture_time_original'), capture_time_end=last.get('capture_time_original')))
        if not images:
            raise ValueError("Không tìm thấy ảnh đọc được")
        job["status"] = "selected"
        self.save(job)

    def prepare(self, job, group, cfg):
        directory = self.directory / job["job_id"] / group["series_id"]
        directory.mkdir(parents=True, exist_ok=True)
        entries, files = [], []
        representative = min((i for i in group['images'] if i['selected']), key=lambda i: i['rank'])['image_id']
        group['representative_image_id'] = representative
        for image in group["images"]:
            self.check_cancel(job)
            if not image["selected"]:
                continue
            source = Path(image["path"])
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != image["hash"]:
                    raise ValueError("Ảnh nguồn đã thay đổi; cần quét lại")
            rgb, _ = read_image(source)
            width, height = rgb.size
            rw, rh = max(1, round(width*cfg["ellipse_ratio"])), max(1, round(height*cfg["ellipse_ratio"]))
            x, y = (width-rw)//2, (height-rh)//2
            bbox = [x, y, x+rw, y+rh]
            thumb = rgb.copy()
            thumb.thumbnail((cfg["thumbnail_dimension"],)*2, Image.Resampling.LANCZOS)
            fields = {}
            outputs = [('thumbnail', thumb)]
            if image['image_id'] == representative:
                crop = rgb.crop(tuple(bbox))
                crop.thumbnail((cfg["crop_dimension"],)*2, Image.Resampling.LANCZOS)
                outputs.append(('primary', crop))
            for kind, output in outputs:
                field = f"{image['image_id']}_{kind}"
                destination = directory / (field+".webp")
                output.save(destination, "WEBP", quality=cfg["quality"])
                if image['image_id'] == representative:
                    files.append((field, destination))
                fields[kind] = field
            preview = rgb.copy()
            preview.thumbnail((800, 800))
            scale = preview.width/width
            ImageDraw.Draw(preview).ellipse(tuple(v*scale for v in bbox), outline="red", width=3)
            preview.save(directory / (image["image_id"]+"_preview.jpg"))
            image['thumbnail_ready'] = True
            image['analysis_representative'] = image['image_id'] == representative
            image["upload_bytes"] = sum(p.stat().st_size for _, p in files) if image['analysis_representative'] else 0
            if not image['analysis_representative']:
                continue
            # Crop coordinates use EXIF-oriented original dimensions, including RAW previews.
            sx, sy = image['original_width']/width, image['original_height']/height
            original_bbox = [round(bbox[0]*sx), round(bbox[1]*sy), round(bbox[2]*sx), round(bbox[3]*sy)]
            entries.append({key: image[key] for key in ["image_id", "filename", "original_width", "original_height", "rank", "score", "warnings"]} |
                           dict(fields=fields, bbox=original_bbox, shape="ellipse", encoding="bounding_rectangle",
                                coordinate_space="exif_oriented_original", mode="rgb", ratio=cfg["ellipse_ratio"]))
        return dict(job_id=job["job_id"], series_id=group["series_id"], images=entries,
                    analysis_scope='series_representative', series_image_count=len(group['images']),
                    selected_image_ids=[i['image_id'] for i in group['images'] if i['selected']],
                    target_tab_id=cfg["target_tab_id"]), files

    @staticmethod
    def headers(cfg):
        return {"Authorization": "Bearer "+cfg["api_key"]} if cfg["api_key"] else {}

    def upload(self, metadata, files, cfg):
        from contextlib import ExitStack
        for attempt in range(cfg["max_retries"]+1):
            if metadata.get('job_id'):
                self.check_cancel(metadata)
            try:
                with ExitStack() as stack:
                    parts = [(field, (path.name, stack.enter_context(path.open("rb")), "image/webp")) for field, path in files]
                    response = requests.post(cfg["server_url"]+"/api/vision/analyze",
                        data={"metadata": json.dumps(metadata)}, files=parts,
                        headers=self.headers(cfg), timeout=cfg["timeout"])
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                elif not response.ok:
                    raise ValueError(f"Upload bị từ chối ({response.status_code})")
                return response.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
                if attempt == cfg["max_retries"]:
                    raise
                if metadata.get('job_id'):
                    self.interruptible_wait(metadata, min(2**attempt, 15))
                else:
                    time.sleep(min(2**attempt, 15))
