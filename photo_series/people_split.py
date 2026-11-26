"""Split groups that mix different people at the same spot (e.g. guests posing one after another at a backdrop).

Local grouping cannot tell people apart (no AI on the user's machine), so a group is cut into time segments wherever
the camera paused for SEGMENT_GAP seconds or more (people need that long to swap places). One thumbnail per segment
goes to the server, several groups per grid; the server answers which segments show the same set of people, and the
group is rebuilt from those parts. Anything unusable keeps the group as it was.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from pathlib import Path

import requests
from PIL import Image

SEGMENT_GAP = 4.0
MAX_TILES = 25
THUMBNAIL = 480
WAIT_SECONDS = 900
log = logging.getLogger(__name__)


def segments_of(group):
    images = sorted(group["images"], key=lambda image: (image["sort_time"], image["image_id"]))
    parts = [[images[0]]]
    for previous, image in zip(images, images[1:]):
        if image["sort_time"]-previous["sort_time"] >= SEGMENT_GAP:
            parts.append([])
        parts[-1].append(image)
    while len(parts) > MAX_TILES:
        # Too many pauses for one grid: join the two neighbours separated by the shortest pause.
        gaps = [b[0]["sort_time"]-a[-1]["sort_time"] for a, b in zip(parts, parts[1:])]
        k = gaps.index(min(gaps))
        parts[k:k+2] = [parts[k]+parts[k+1]]
    return parts


def rebuild(images, n):
    images = sorted(images, key=lambda image: (-image["score"], image["image_id"]))
    for rank, image in enumerate(images, 1):
        image |= dict(rank=rank, selected=rank <= n, analysis_representative=rank == 1)
    first = min(images, key=lambda image: image["sort_time"])
    last = max(images, key=lambda image: image["sort_time"])
    return dict(series_id=hashlib.sha256("|".join(sorted(i["image_id"] for i in images)).encode()).hexdigest()[:24],
                status="selected", images=images, representative_image_id=images[0]["image_id"],
                capture_time_start=first.get("capture_time_original"), capture_time_end=last.get("capture_time_original"),
                split_from_people=True)


def thumbnail(pipeline, image, folder):
    from .image_source import read_image
    path = folder/(image["image_id"]+".webp")
    if not path.exists():
        rgb, _ = read_image(Path(image["path"]), THUMBNAIL)  # reduced JPEG decode or embedded RAW preview
        rgb.thumbnail((THUMBNAIL, THUMBNAIL), Image.Resampling.LANCZOS)
        rgb.save(path, "WEBP", quality=85)
    return path.read_bytes()


def split_people(pipeline, job, cfg):
    """Mutates job['series']; returns a small summary stored on the job."""
    candidates = []
    for group in job["series"]:
        if len(group.get("images") or []) < 2:
            continue
        parts = segments_of(group)
        if len(parts) >= 2:
            candidates.append((group, parts, [max(part, key=lambda image: image["score"]) for part in parts]))
    summary = dict(checked=len(candidates), split=0, added=0, failed_requests=0)
    if not candidates:
        return summary
    grids, current = [], []
    for candidate in candidates:
        if current and sum(len(c[2]) for c in current)+len(candidate[2]) > MAX_TILES:
            grids.append(current)
            current = []
        current.append(candidate)
    grids.append(current)
    folder = pipeline.directory/job["job_id"]/"people-split"
    folder.mkdir(parents=True, exist_ok=True)

    def submit(grid, attempt):
        clusters = [[tile["image_id"] for tile in tiles] for _, _, tiles in grid]
        files = {tile["image_id"]: (tile["image_id"]+".webp", thumbnail(pipeline, tile, folder), "image/webp")
                 for _, _, tiles in grid for tile in tiles}
        metadata = dict(job_id=job["job_id"], clusters=clusters, attempt=attempt)
        for retry in range(cfg["max_retries"]+1):
            pipeline.check_cancel(job)
            try:
                response = requests.post(cfg["server_url"]+"/api/vision/split", data={"metadata": json.dumps(metadata)},
                                         files=files, headers=pipeline.headers(cfg), timeout=cfg["timeout"])
                if response.status_code == 503:
                    raise requests.ConnectionError("no processor")
                response.raise_for_status()
                return response.json()["split_id"]
            except (requests.ConnectionError, requests.Timeout):
                if retry == cfg["max_retries"]:
                    raise
                pipeline.interruptible_wait(job, min(30, 2**retry))

    def wait(split_id):
        deadline = time.monotonic()+WAIT_SECONDS
        while time.monotonic() < deadline:
            pipeline.check_cancel(job)
            try:
                response = requests.get(cfg["server_url"]+"/api/vision/split/"+split_id, headers=pipeline.headers(cfg), timeout=cfg["timeout"])
                response.raise_for_status()
                data = response.json()
                if data["status"] in ("completed", "failed", "cancelled"):
                    return data
            except (requests.ConnectionError, requests.Timeout):
                pass
            pipeline.interruptible_wait(job, cfg["poll_seconds"])
        return dict(status="failed", groups=None)

    answers = {}
    pending = []
    for index, grid in enumerate(grids):
        try:
            pending.append((index, submit(grid, 0)))
        except requests.RequestException:
            log.exception("People split request could not be sent")
            summary["failed_requests"] += 1
    for index, split_id in pending:
        data = wait(split_id)
        if data["status"] != "completed":
            try:
                data = wait(submit(grids[index], 1))
            except requests.RequestException:
                log.exception("People split retry could not be sent")
        if data["status"] == "completed":
            answers[index] = data["groups"]
        else:
            summary["failed_requests"] += 1

    replacements = {}
    for index, grid in enumerate(grids):
        for (group, parts, tiles), groups in zip(grid, answers.get(index) or [None]*len(grid)):
            if not groups or len(groups) < 2:
                continue
            segment_of = {tile["image_id"]: part for tile, part in zip(tiles, parts)}
            subgroups = [rebuild([image for tile_id in tile_ids for image in segment_of[tile_id]], cfg["n"]) for tile_ids in groups]
            subgroups.sort(key=lambda g: min(i["sort_time"] for i in g["images"]))
            replacements[group["series_id"]] = subgroups
            summary["split"] += 1
            summary["added"] += len(subgroups)-1
    if replacements:
        series = []
        for group in job["series"]:
            series.extend(replacements.get(group["series_id"], [group]))
        job["series"] = series
    return summary
