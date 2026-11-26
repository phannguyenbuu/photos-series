import io
import json
import tempfile
import unittest
import threading
import time
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageFilter
from flask import Flask
from .pipeline import Pipeline, configuration
from .bridge_routes import register_photo_series_bridge, parse_result
from .local_routes import register_photo_series_routes
from .image_source import read_image
from .grouping import compare_scene


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def photos(self):
        folder = self.root / "photos"
        folder.mkdir()
        rng = np.random.default_rng(123)
        for group in range(5):
            source = Image.fromarray(rng.integers(25, 230, (16, 24, 3), dtype=np.uint8)).resize((240, 160), Image.Resampling.BICUBIC)
            for index in range(10):
                image = source.filter(ImageFilter.GaussianBlur(index*.1))
                exif = Image.Exif()
                exif[36867] = f"2026:11:16 10:0{group}:{index:02}"
                image.save(folder/f"{group}_{index}.jpg", exif=exif)
        return folder

    def test_fifty_images_group_select_crop_cache(self):
        folder = self.photos()
        original = {p.name: p.read_bytes() for p in folder.iterdir()}
        pipeline = Pipeline(self.root/"output")
        cfg = configuration(dict(n=2, similarity_threshold=.7))
        job = dict(job_id="test", folder=str(folder))
        pipeline.scan(job, cfg)
        self.assertEqual(job["processed"], 50)
        self.assertEqual(len(job["series"]), 5)
        self.assertEqual(sum(i["selected"] for s in job["series"] for i in s["images"]), 10)
        for group in job["series"]:
            self.assertEqual(group["images"][0]["sharpness"], max(i["sharpness"] for i in group["images"]))
            metadata, files = pipeline.prepare(job, group, cfg)
            self.assertEqual(len(files), 2)
            self.assertEqual(len(metadata['images']), 1)
            self.assertEqual(sum(bool(i.get('upload_bytes')) for i in group['images']), 1)
            self.assertEqual(sum(bool(i.get('thumbnail_ready')) for i in group['images']), 2)
            self.assertEqual(metadata["images"][0]["bbox"], [40, 26, 200, 133])
            for _, path in files:
                with Image.open(path) as image:
                    self.assertEqual(image.mode, "RGB")
        with patch("photo_series.pipeline.Image.open", side_effect=AssertionError("should use cache")):
            cached = pipeline.inspect(folder/"0_0.jpg", cfg)
            self.assertEqual(cached["filename"], "0_0.jpg")
        self.assertEqual(original, {p.name: p.read_bytes() for p in folder.iterdir()})

    def test_export_best_copies_with_context_and_is_not_rescanned(self):
        import piexif
        from .export import export_best
        folder = self.photos()
        original = {p.name: p.read_bytes() for p in folder.iterdir()}
        pipeline = Pipeline(self.root/"output")
        cfg = configuration(dict(n=3, similarity_threshold=.7))
        job = dict(job_id="export", folder=str(folder))
        pipeline.scan(job, cfg)
        raw = folder/"raw_only.ARW"
        raw.write_bytes(b"not-a-real-raw")
        for k, group in enumerate(job["series"]):
            group["result"] = dict(context=f"Cô dâu chú rể nhóm {k} — ảnh cưới")
        best = min(job["series"][0]["images"], key=lambda i: i["rank"])
        best["source_files"] = [best["path"], str(raw)]
        stats = export_best(job, 2)
        target = Path(stats["folder"])
        self.assertEqual(target, folder/"selected")
        self.assertEqual(stats["images"], 10)
        self.assertEqual(stats["files"], 11)
        self.assertEqual((stats["exif"], stats["sidecars"], stats["errors"]), (10, 1, []))
        self.assertIn("nhóm 0", (target/"raw_only.xmp").read_text(encoding="utf-8"))
        self.assertEqual((target/"raw_only.ARW").read_bytes(), b"not-a-real-raw")
        expected = {i["filename"] for g in job["series"] for i in sorted(g["images"], key=lambda i: i["rank"])[:2]}
        self.assertEqual({p.name for p in target.glob("*.jpg")}, expected)
        copy = target/best["filename"]
        tags = piexif.load(str(copy))
        self.assertEqual(tags["0th"][piexif.ImageIFD.ImageDescription].decode("utf-8"), "Cô dâu chú rể nhóm 0 — ảnh cưới")
        with Image.open(copy) as a, Image.open(folder/best["filename"]) as b:
            self.assertEqual(a.tobytes(), b.tobytes())  # pixels not re-encoded
        self.assertEqual(original, {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file() and p.name != "raw_only.ARW"})
        rescan = dict(job_id="rescan", folder=str(folder))
        pipeline.scan(rescan, cfg)
        self.assertEqual(rescan["total_files"], 51)
        self.assertEqual(Path(export_best(job, 1)["folder"]), folder/"selected_2")

    def test_bridge_grid_batches_and_splits_results(self):
        from . import bridge_routes
        class Orchestrator:
            lock = threading.RLock()
            def __init__(inner):
                inner.jobs, inner.created = [], []
            def get_all_workers(inner):
                return [dict(worker_id='g1', provider='gemini', is_online=True, is_enabled=True, can_receive_jobs=True, bridge_version='5.6.4'),
                        dict(worker_id='c1', provider='chatgpt', is_online=True, is_enabled=True, can_receive_jobs=True, bridge_version='5.6.4')]
            def create_job(inner, **kwargs):
                job = dict(id=f"remote{len(inner.created)+1}", status="queued", **kwargs)
                inner.created.append(job); inner.jobs.append(job)
                return job
            def get_job(inner, job_id):
                return next((job for job in inner.jobs if job["id"] == job_id), None)
        backend = Orchestrator()
        app = Flask(__name__)
        with patch.object(bridge_routes, "GRID_QUIET", .2):
            register_photo_series_bridge(app, backend, self.root/"bridge")
            self.addCleanup(lambda: (app.extensions['photo_series_stop'].set(), time.sleep(1.2)))
            client = app.test_client()
            def payload(series, image):
                data = {"metadata": json.dumps(dict(job_id="local1", series_id=series, images=[dict(image_id=image, fields=dict(thumbnail="t", primary="p"))]))}
                for key in ("t", "p"):
                    buffer = io.BytesIO(); Image.new("RGB", (48, 32), "red").save(buffer, "WEBP"); buffer.seek(0)
                    data[key] = (buffer, key+".webp")
                return data
            def wait_created(count):
                for _ in range(100):
                    if len(backend.created) >= count:
                        return backend.created[count-1]
                    time.sleep(.05)
                self.fail("grid request not created")
            ids = {}
            for k in (1, 2, 3):
                response = client.post("/api/vision/analyze", data=payload(f"s{k}", f"img{k}"))
                self.assertEqual(response.status_code, 202)
                ids[k] = response.json["job_id"]
                self.assertTrue(ids[k].startswith("ps_"))
            again = client.post("/api/vision/analyze", data=payload("s1", "img1"))
            self.assertEqual((again.status_code, again.json["job_id"]), (200, ids[1]))
            grid = wait_created(1)
            self.assertEqual(len(grid["reference_images"]), 1)
            self.assertEqual(grid["target_worker_id"], "g1")
            self.assertTrue(grid["fresh_conversation"])
            self.assertIn("lưới 2x2 gồm 3 ô", grid["prompt"])
            self.assertIn("ô 3 = img3", grid["prompt"])
            self.assertNotEqual(client.get(f"/api/vision/series/{ids[1]}").json["status"], "completed")
            grid.update(status="completed", result_text="Gemini đã nói\n"+json.dumps(dict(context="lưới 3 ô", images=[
                dict(image_id="img1", tile=1, context="Cô dâu chú rể", people_count=2),
                dict(image_id="img2", tile=2, context="Không có dữ liệu hình ảnh cho ô 2", people_count=None)])))
            first = client.get(f"/api/vision/series/{ids[1]}").json
            self.assertEqual(first["status"], "completed")
            self.assertEqual(first["result"]["context"], "Cô dâu chú rể")
            self.assertEqual(first["result"]["images"], [dict(image_id="img1", people_count=2, faces=[])])
            self.assertEqual(client.get(f"/api/vision/series/{ids[2]}").json["status"], "failed")
            self.assertEqual(client.get(f"/api/vision/series/{ids[3]}").json["status"], "failed")
            retried = client.post(f"/api/vision/series/{ids[2]}/retry")
            self.assertEqual(retried.status_code, 202)
            self.assertEqual(client.post(f"/api/vision/series/{ids[2]}/retry").json["job_id"], retried.json["job_id"])
            second = wait_created(2)
            self.assertIn("ô 1 = img2", second["prompt"])
            second.update(status="completed", result_text=json.dumps(dict(context="lưới 1 ô", images=[dict(image_id="img2", tile=1, context="Xe hoa", people_count=0)])))
            self.assertEqual(client.get(f"/api/vision/series/{retried.json['job_id']}").json["result"]["context"], "Xe hoa")
        restarted = Flask("restarted")
        register_photo_series_bridge(restarted, backend, self.root/"bridge")
        self.addCleanup(lambda: (restarted.extensions['photo_series_stop'].set(), time.sleep(1.2)))
        cached = restarted.test_client().get(f"/api/vision/series/{ids[1]}")
        self.assertEqual(cached.status_code, 200)
        self.assertTrue(cached.json["structured_result_valid"])

    def test_grid_compose_and_split(self):
        from . import grid_batch
        buffer = io.BytesIO(); Image.new("RGB", (480, 320), "blue").save(buffer, "WEBP")
        url, n = grid_batch.compose([buffer.getvalue()]*16)
        self.assertEqual(n, 4)
        with Image.open(io.BytesIO(__import__("base64").b64decode(url.split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (4*480+5*20,)*2)
        ids = ["a", "b", "c"]
        text = json.dumps(dict(context="x", images=[dict(image_id="a", tile=1, context="ok"), dict(image_id="b", tile=3, context="lệch ô"),
                                                    dict(image_id="c", tile=3, context="", people_count=1)]))
        self.assertEqual(list(grid_batch.split(text, ids)), ["a"])
        self.assertEqual(grid_batch.split('{"context":"x","images":[', ids), {})

    def test_incomplete_results_are_rejected(self):
        metadata = dict(images=[dict(image_id="one")])
        self.assertIsNone(parse_result('{"context":"partial", "images":[', metadata))
        self.assertIsNone(parse_result('{"context":"done", "images":[{"image_id":"wrong"}]}', metadata))
        self.assertIsNotNone(parse_result('JSON{"context":"done", "images":[{"image_id":"one"}]}', metadata))

    def test_local_origin_and_mutation_guard(self):
        app = Flask(__name__)
        register_photo_series_routes(app, self.root/"local")
        client = app.test_client()
        self.assertEqual(client.post("/api/photo-series/jobs", json={}).status_code, 403)
        self.assertEqual(client.get("/api/photo-series/jobs/x", headers={"Origin": "https://evil.example"}).status_code, 403)
        response = client.options("/api/photo-series/jobs", headers={"Origin": "https://dir.toolxprint.com"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://dir.toolxprint.com")
        self.assertEqual(client.get("/api/photo-series/jobs/x", headers={"Origin": "https://genai.toolxprint.com"}).status_code, 403)

    def test_source_change_rejected_and_retry_resume_skips_completed(self):
        folder = self.photos()
        pipeline = Pipeline(self.root/"output")
        cfg = configuration(dict(n=1, similarity_threshold=.7))
        job = dict(job_id="test", folder=str(folder))
        pipeline.scan(job, cfg)
        source = Path(job["series"][0]["images"][0]["path"])
        source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            pipeline.prepare(job, job["series"][0], cfg)
        for group in job["series"]:
            group["status"] = "completed"
        with patch.object(pipeline, "upload", side_effect=AssertionError("no reupload")):
            pipeline.run(job, cfg)
        self.assertEqual(pipeline.get("test")["status"], "completed")

    def test_local_to_remote_state_machine(self):
        pipeline = Pipeline(self.root/"state-machine")
        group = dict(series_id="s", status="selected", images=[])
        job = dict(job_id="state-machine", series=[group])
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return dict(job_id="remote", status="completed", result={"context": "test"}, result_text="test")
        with patch.object(pipeline, "prepare", return_value=({}, [])), patch.object(pipeline, "upload", return_value={"job_id": "remote"}), patch("photo_series.pipeline.requests.get", return_value=Response()):
            pipeline.run(job, configuration({}))
        self.assertEqual(job["status"], "completed")
        self.assertEqual(group["result"]["context"], "test")

    def test_raw_sidecars_are_one_capture(self):
        folder = self.photos()
        (folder/'0_0.ARW').write_bytes(b'paired RAW is retained, JPEG is the analysis source')
        pipeline = Pipeline(self.root/'paired')
        job = dict(job_id='paired', folder=str(folder))
        pipeline.scan(job, configuration({'n': 3, 'similarity_threshold': .7}))
        self.assertEqual(job['total'], 50)
        self.assertEqual(job['total_files'], 51)
        self.assertEqual(job['raw_files'], 1)
        match = next(i for s in job['series'] for i in s['images'] if i['filename'] == '0_0.jpg')
        self.assertEqual(set(match['formats']), {'JPG', 'ARW'})

    def test_auto_time_grouping_follows_capture_cadence(self):
        folder = self.root/'cadence'
        folder.mkdir()
        image = Image.fromarray(np.random.default_rng(1).integers(20, 230, (80, 120, 3), dtype=np.uint8))
        for index in range(5):
            exif = Image.Exif()
            exif[36867] = f'2026:11:16 10:00:{index*12:02}'
            image.save(folder/f'{index}.jpg', exif=exif)
        pipeline = Pipeline(self.root/'auto')
        job = dict(job_id='auto', folder=str(folder))
        pipeline.scan(job, configuration({}))
        self.assertEqual(len(job['series']), 1)
        self.assertGreater(job['grouping']['inferred_gap_seconds'], 12)

    def test_health_and_manual_folder_picker(self):
        app = Flask(__name__)
        register_photo_series_routes(app, self.root/'picker')
        client = app.test_client()
        self.assertEqual(client.get('/api/photo-series/health').json['analysis_images_per_series'], 1)
        headers = {'Origin': 'https://dir.toolxprint.com', 'X-Photo-Series': '1'}
        with patch('tkinter.Tk'), patch('tkinter.filedialog.askdirectory', return_value=str(self.root)):
            response = client.post('/api/photo-series/pick-folder', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['folder'], str(self.root.resolve()))
        with patch('tkinter.Tk'), patch('tkinter.filedialog.askdirectory', return_value=''):
            self.assertTrue(client.post('/api/photo-series/pick-folder', headers=headers).json['cancelled'])

    def test_scene_matching_handles_rotation_and_rejects_unrelated_content(self):
        folder = self.root/'scene'
        folder.mkdir()
        rng = np.random.default_rng(17)
        data = rng.integers(25, 225, (100, 140, 3), dtype=np.uint8)
        original = Image.fromarray(data).resize((700, 500))
        original.save(folder/'a.jpg')
        original.rotate(90, expand=True).save(folder/'b.jpg')
        Image.fromarray(rng.integers(25, 225, (100, 140, 3), dtype=np.uint8)).resize((700, 500)).save(folder/'different.jpg')
        p = Pipeline(self.root/'scene-cache')
        cfg = configuration({})
        a, b, other = [p.inspect(folder/name, cfg) for name in ('a.jpg', 'b.jpg', 'different.jpg')]
        self.assertTrue(compare_scene(a, b)['same_scene'])
        self.assertFalse(compare_scene(a, other)['same_scene'])

    def test_exif_offset_and_subsecond_are_preserved(self):
        times = [('a.jpg', '2026:11:17 10:00:00', '+07:00'), ('b.jpg', '2026:11:17 03:00:00', '+00:00')]
        metadata = []
        for name, date, offset in times:
            exif = Image.Exif()
            exif[36867] = date
            exif[36881] = offset
            exif[37521] = '350'
            path = self.root/name
            Image.new('RGB', (50, 80), 'red').save(path, exif=exif)
            metadata.append(read_image(path)[1])
        self.assertEqual(metadata[0]['captured_at'], metadata[1]['captured_at'])
        self.assertAlmostEqual(metadata[0]['captured_at']%1, .35, places=5)

    def test_cancel_stops_uploads_preserves_completed_and_freezes_elapsed(self):
        p = Pipeline(self.root/'cancel')
        completed = dict(series_id='done', status='completed', images=[], result={'context': 'keep'})
        job = dict(job_id='cancel-job', status='selected', series=[completed]+[dict(series_id=str(i), status='selected', images=[]) for i in range(8)])
        p.save(job)
        uploading, release = threading.Event(), threading.Event()
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'cancelled': True}
        def upload(*args):
            uploading.set()
            release.wait(2)
            return {'job_id': 'remote-in-flight'}
        with patch.object(p, 'prepare', return_value=({'job_id': job['job_id']}, [])), patch.object(p, 'upload', side_effect=upload) as mocked_upload, patch('photo_series.pipeline.requests.post', return_value=Response()) as post, patch('photo_series.pipeline.requests.get', side_effect=AssertionError('cancelled run must not poll')):
            worker = threading.Thread(target=p.run, args=(job, configuration({'concurrent_uploads': 1})))
            worker.start()
            self.assertTrue(uploading.wait(2))
            self.assertEqual(p.request_cancel(job['job_id'])['status'], 'cancelling')
            release.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(mocked_upload.call_count, 1)
            self.assertTrue(any(call.args[0].endswith('/cancel') for call in post.call_args_list))
        stored = p.get(job['job_id'])
        self.assertEqual(stored['status'], 'cancelled')
        self.assertEqual(stored['series'][0]['result']['context'], 'keep')
        frozen = p.elapsed(stored)
        time.sleep(.02)
        self.assertEqual(p.elapsed(stored), frozen)
        self.assertGreaterEqual(frozen, 0)

    def test_bridge_cancel_is_durable_and_blocks_late_uploads(self):
        class Backend:
            def __init__(self): self.lock = threading.RLock(); self.jobs = []
            def get_all_workers(self): return [dict(worker_id='test', provider='chatgpt', is_online=True, is_enabled=True, can_receive_jobs=True, bridge_version='5.6.3')]
            def create_job(self, **kwargs):
                job = {'id': 'cancel-remote', 'status': 'processing', **kwargs}
                self.jobs.append(job)
                return job
            def get_job(self, job_id): return next((dict(job) for job in self.jobs if job['id'] == job_id), None)
        backend = Backend()
        app = Flask('cancel-bridge')
        register_photo_series_bridge(app, backend, self.root/'cancel-bridge')
        self.addCleanup(lambda: (app.extensions['photo_series_stop'].set(), time.sleep(1.2)))
        client = app.test_client()
        metadata = dict(job_id='parent', series_id='one', images=[dict(image_id='img', fields={'thumbnail': 't', 'primary': 'p'})])
        def payload():
            files = {'metadata': json.dumps(metadata)}
            for key in ('t', 'p'):
                buffer = io.BytesIO()
                Image.new('RGB', (20, 20), 'green').save(buffer, 'WEBP')
                buffer.seek(0)
                files[key] = (buffer, key+'.webp')
            return files
        first = client.post('/api/vision/analyze', data=payload())
        self.assertEqual(first.status_code, 202)
        series_id = first.json['job_id']
        self.assertTrue(client.post('/api/vision/jobs/parent/cancel').json['cancelled'])
        self.assertEqual(client.get(f'/api/vision/series/{series_id}').json['status'], 'cancelled')
        self.assertEqual(client.post('/api/vision/analyze', data=payload()).status_code, 409)
        time.sleep(1.5)
        self.assertEqual(backend.jobs, [], 'cancelled series must not be sent in a grid')
        restarted = Flask('cancel-restarted')
        register_photo_series_bridge(restarted, backend, self.root/'cancel-bridge')
        self.addCleanup(lambda: (restarted.extensions['photo_series_stop'].set(), time.sleep(1.2)))
        self.assertEqual(restarted.test_client().get(f'/api/vision/series/{series_id}').json['status'], 'cancelled')

    def test_people_split_answer_parsing(self):
        from . import grid_batch
        good = '{"groups":[{"tiles":[1,2]},{"tiles":[3]},{"tiles":[4,5]}]}'
        self.assertEqual(grid_batch.people_split(good, [3, 2]), [[[0, 1], [2]], [[0, 1]]])
        crossing = '{"groups":[{"tiles":[1,2,4]},{"tiles":[3]},{"tiles":[5]}]}'
        self.assertEqual(grid_batch.people_split(crossing, [3, 2]), [None, None])
        missing = '{"groups":[{"tiles":[1]},{"tiles":[4,5]}]}'
        self.assertEqual(grid_batch.people_split(missing, [3, 2]), [None, [[0, 1]]])
        self.assertEqual(grid_batch.people_split("Tôi không được lập trình để làm điều đó.", [3, 2]), [None, None])
        self.assertIn("cụm 2: ô 4-5", grid_batch.people_prompt([3, 2], 3))

    def test_split_people_rebuilds_groups_from_server_answer(self):
        from . import people_split
        folder = self.root/"people"; folder.mkdir()
        images = []
        for k, (second, score) in enumerate([(0, 50), (1, 90), (10, 70), (11, 60), (30, 80), (31, 40)]):
            path = folder/f"p{k}.jpg"; Image.new("RGB", (60, 40), (k*30, 80, 120)).save(path)
            images.append(dict(image_id=f"img{k}", filename=path.name, path=str(path), sort_time=1000+second, score=score))
        job = dict(job_id="people", series=[dict(series_id="orig", status="selected", images=images)])
        pipeline = Pipeline(self.root/"store")
        cfg = configuration(dict(n=1, poll_seconds=1))
        sent = {}
        class Response:
            def __init__(self, data, code=200): self.data, self.status_code = data, code
            def json(self): return self.data
            def raise_for_status(self): pass
        def post(url, data, files, headers, timeout):
            sent["clusters"] = json.loads(data["metadata"])["clusters"]
            self.assertEqual(len(files), 3)
            return Response(dict(split_id="split_1"), 202)
        def get(url, headers, timeout):
            a, b, c = sent["clusters"][0]
            return Response(dict(status="completed", groups=[[[a, c], [b]]]))
        with patch.object(people_split.requests, "post", post), patch.object(people_split.requests, "get", get):
            summary = people_split.split_people(pipeline, job, cfg)
        self.assertEqual(sent["clusters"], [["img1", "img2", "img4"]])  # best score of each pause-separated segment
        self.assertEqual((summary["split"], summary["added"]), (1, 1))
        first, second = job["series"]
        self.assertEqual(sorted(i["image_id"] for i in first["images"]), ["img0", "img1", "img4", "img5"])
        self.assertEqual(sorted(i["image_id"] for i in second["images"]), ["img2", "img3"])
        self.assertEqual((first["representative_image_id"], second["representative_image_id"]), ("img1", "img2"))
        self.assertEqual([i["selected"] for i in second["images"]], [True, False])

    def test_bridge_people_split_endpoint(self):
        class Orchestrator:
            lock = threading.RLock()
            def __init__(inner): inner.jobs = []
            def get_all_workers(inner):
                return [dict(worker_id='g1', provider='gemini', is_online=True, is_enabled=True, can_receive_jobs=True, bridge_version='5.6.4')]
            def create_job(inner, **kwargs):
                job = dict(id=f"r{len(inner.jobs)}", status="queued", **kwargs); inner.jobs.append(job); return job
            def get_job(inner, job_id): return next((j for j in inner.jobs if j["id"] == job_id), None)
        backend = Orchestrator()
        app = Flask("split")
        register_photo_series_bridge(app, backend, self.root/"split-bridge")
        self.addCleanup(lambda: (app.extensions['photo_series_stop'].set(), time.sleep(1.2)))
        client = app.test_client()
        def form(attempt=0):
            data = {"metadata": json.dumps(dict(job_id="local", clusters=[["a", "b", "c"], ["d", "e"]], attempt=attempt))}
            for key in "abcde":
                buffer = io.BytesIO(); Image.new("RGB", (48, 32), "blue").save(buffer, "WEBP"); buffer.seek(0)
                data[key] = (buffer, key+".webp")
            return data
        created = client.post("/api/vision/split", data=form())
        self.assertEqual(created.status_code, 202)
        self.assertEqual(client.post("/api/vision/split", data=form()).json["split_id"], created.json["split_id"])
        self.assertEqual(len(backend.jobs), 1)
        self.assertIn("cụm 1: ô 1-3", backend.jobs[0]["prompt"])
        self.assertTrue(backend.jobs[0]["fresh_conversation"])
        backend.jobs[0].update(status="completed", result_text='{"groups":[{"tiles":[1,3]},{"tiles":[2]},{"tiles":[4,5]}]}')
        result = client.get(f"/api/vision/split/{created.json['split_id']}").json
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["groups"], [[["a", "c"], ["b"]], [["d", "e"]]])
        self.assertEqual(client.post("/api/vision/split", data=form(1)).status_code, 202)

    def test_bridge_people_split_waits_for_busy_tab(self):
        class Orchestrator:
            lock = threading.RLock()
            def __init__(inner): inner.jobs, inner.free = [], False
            def get_all_workers(inner):
                return [dict(worker_id='g1', provider='gemini', is_online=True, is_enabled=True, can_receive_jobs=inner.free, bridge_version='5.6.4')]
            def create_job(inner, **kwargs):
                job = dict(id=f"r{len(inner.jobs)}", status="queued", **kwargs); inner.jobs.append(job); return job
            def get_job(inner, job_id): return next((j for j in inner.jobs if j["id"] == job_id), None)
        backend = Orchestrator()
        app = Flask("split-busy")
        register_photo_series_bridge(app, backend, self.root/"split-busy")
        self.addCleanup(lambda: (app.extensions['photo_series_stop'].set(), time.sleep(1.2)))
        client = app.test_client()
        data = {"metadata": json.dumps(dict(job_id="local", clusters=[["a", "b"]]))}
        for key in "ab":
            buffer = io.BytesIO(); Image.new("RGB", (48, 32), "blue").save(buffer, "WEBP"); buffer.seek(0)
            data[key] = (buffer, key+".webp")
        created = client.post("/api/vision/split", data=data)
        self.assertEqual(created.status_code, 202)
        self.assertEqual(client.get(f"/api/vision/split/{created.json['split_id']}").json["status"], "queued")
        backend.free = True
        for _ in range(40):
            if backend.jobs: break
            time.sleep(.1)
        self.assertEqual(len(backend.jobs), 1)

    def test_image_endpoint_serves_unselected_photos(self):
        photo = self.root/"u.jpg"; Image.new("RGB", (900, 600), "green").save(photo)
        app = Flask("images")
        register_photo_series_routes(app, self.root/"agent")
        pipeline = Pipeline(self.root/"agent")
        pipeline.save(dict(job_id="j1", status="completed", series=[dict(series_id="s1", images=[
            dict(image_id="sel", selected=True, path=str(photo)), dict(image_id="other", selected=False, path=str(photo))])]))
        client = app.test_client()
        response = client.get("/api/photo-series/jobs/j1/images/other/thumbnail")
        self.assertEqual(response.status_code, 200)
        with Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual((image.format, max(image.size)), ("WEBP", 480))
        self.assertEqual(client.get("/api/photo-series/jobs/j1/images/other/preview").status_code, 200)
        self.assertEqual(client.get("/api/photo-series/jobs/j1/images/other/primary").status_code, 404)
        self.assertEqual(client.get("/api/photo-series/jobs/j1/images/missing/thumbnail").status_code, 404)

    def test_face_boxes_map_from_grid_to_tile(self):
        from . import grid_batch
        buffer = io.BytesIO(); Image.new("RGB", (480, 320), "blue").save(buffer, "WEBP")
        _, n, layout = grid_batch.compose_with_layout([buffer.getvalue()]*4)
        self.assertEqual((n, layout["size"]), (2, 1020))
        self.assertEqual(layout["tiles"][3], [520, 600, 480, 320])
        size = layout["size"]
        # A face covering the middle of tile 4 (x 0.4-0.6, y 0.25-0.75 of the photo), in grid 0-1000 coordinates.
        grid_box = [round((600+80)/size*1000), round((520+192)/size*1000), round((600+240)/size*1000), round((520+288)/size*1000)]
        [face] = grid_batch.faces_in_tile([grid_box], layout, 3)
        self.assertTrue(all(abs(a-b) < .01 for a, b in zip(face, [.4, .25, .6, .75])), face)
        # Answered relative to the tile itself: centre lands outside tile 1, so it is read as tile-relative.
        self.assertEqual(grid_batch.faces_in_tile([[100, 700, 300, 900]], layout, 0), [[.7, .1, .9, .3]])
        self.assertEqual(grid_batch.faces_in_tile(["bad", [500, 500, 400, 600], [0, 0, 0, 0], [10, 10, 2000, 20]], layout, 3), [])
        self.assertIn("faces:[[number,number,number,number]]", grid_batch.prompt(["a", "b"], 2))

    def test_extra_origins_from_settings_are_exact(self):
        agent = self.root/"GoxPrintAgent"; (agent/"photo-series").mkdir(parents=True)
        (agent/"settings.json").write_text(json.dumps({"web": {"allowed_origins": ["https://shop.example.com/", "*"]}}), encoding="utf-8")
        app = Flask("origins")
        register_photo_series_routes(app, agent/"photo-series")
        client = app.test_client()
        ok = client.get("/api/photo-series/health", headers={"Origin": "https://shop.example.com"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers["Access-Control-Allow-Origin"], "https://shop.example.com")
        for origin in ("https://shop.example.com.evil.test", "http://shop.example.com", "https://evil.test"):
            self.assertEqual(client.get("/api/photo-series/health", headers={"Origin": origin}).status_code, 403, origin)
        self.assertEqual(client.get("/api/photo-series/health", headers={"Origin": "https://dir.toolxprint.com"}).status_code, 200)

if __name__ == "__main__":
    unittest.main()
