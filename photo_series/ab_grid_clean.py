"""Clean Gemini comparison of 2x2, 3x3 and 4x4 full-frame grids on the same 32 groups.

Every request starts in a new Gemini conversation (requested through the bridge before submitting),
so no answer can be copied from earlier turns. Timing is the bridge's duration_seconds.
"""
import base64, io, json, random, sqlite3, statistics, sys, time
from pathlib import Path
import requests
import ab_tiling as m
from ab_grid_gemini import grid_image, prompt

sys.stdout.reconfigure(encoding="utf-8")
GROUPS, SEED = 32, 23
B = m.BRIDGE


def gemini_worker():
    ws = requests.get(B+"/api/workers", timeout=20).json(); ws = ws.get("workers", ws) if isinstance(ws, dict) else ws
    return next(w for w in ws if w.get("provider") == "gemini" and w.get("is_online") and w.get("can_receive_jobs"))


def fresh_chat(worker_id):
    requests.post(B+"/api/chatgpt/new_chat", json=dict(worker_id=worker_id), timeout=20)
    deadline = time.time()+90
    while time.time() < deadline:
        time.sleep(2)
        w = gemini_worker()
        if w["worker_id"] == worker_id and w.get("conversation_url", "").rstrip("/").endswith("/app") and w.get("executor_status") == "idle":
            return True
    raise RuntimeError("Gemini tab did not open a new conversation")


def run(worker_id, n, name, chunk):
    fresh_chat(worker_id)
    image, cell = grid_image([Path(g["path"]) for g in chunk], n)
    image.save(m.OUT/f"clean_grid{n}_{name}.jpg", quality=90)
    buf = io.BytesIO(); image.save(buf, "JPEG", quality=90)
    ids = [g["image_id"] for g in chunk]
    job = requests.post(B+"/api/chat/create", json=dict(prompt=prompt(ids, n), reference_images=["data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()],
                        target_tab_id=worker_id, job_type="chat_code"), timeout=60).json()
    while True:
        time.sleep(3)
        s = requests.get(B+"/api/jobs/"+job["id"], timeout=20).json()
        if s["status"] in ("completed", "failed", "cancelled"):
            break
    parsed = m.parse(s.get("result_text") or "") if s["status"] == "completed" else None
    items = {i.get("image_id"): i for i in (parsed or {}).get("images", []) if isinstance(i, dict) and i.get("image_id") in ids}
    print(f"{n}x{n} #{name}: {s['status']} {s.get('duration_seconds')} s, {len(items)}/{len(ids)} ô, ô {cell}px", flush=True)
    return dict(n=n, name=name, job_id=job["id"], status=s["status"], seconds=s.get("duration_seconds"), cell=cell, ids=ids, items=items,
                raw=(s.get("result_text") or "")[:4000])


def main():
    con = sqlite3.connect(m.STORE/"state.sqlite3")
    if [i for i, s in con.execute("SELECT id,state FROM jobs") if json.loads(s).get("status") in m.LOCAL_BUSY]:
        sys.exit("Local job running; not starting")
    job = json.loads(con.execute("SELECT state FROM jobs WHERE id=?", (m.JOB,)).fetchone()[0])
    pool = []
    for g in job["series"]:
        rep = g.get("representative_image_id")
        if g["status"] == "completed" and rep and g.get("result"):
            image = next(i for i in g["images"] if i["image_id"] == rep)
            if Path(image["path"]).suffix.lower() in (".jpg", ".jpeg") and Path(image["path"]).is_file():
                base = next((x for x in g["result"]["images"] if x["image_id"] == rep), {})
                pool.append(dict(image_id=rep, filename=image["filename"], path=image["path"], baseline=base, series_context=g["result"]["context"]))
    chosen = random.Random(SEED).sample(pool, GROUPS)
    base = {g["image_id"]: g for g in chosen}
    worker = gemini_worker()["worker_id"]
    print("worker", worker, "groups", len(chosen), flush=True)
    plan = [(n, k//(n*n)+1, chosen[k:k+n*n]) for n in (4, 3, 2) for k in range(0, GROUPS, n*n)]
    # Interleave grid sizes so time-of-day drift affects all sizes alike.
    plan.sort(key=lambda p: (p[1], -p[0]))
    runs = []
    for n, name, chunk in plan:
        try:
            runs.append(run(worker, n, name, chunk))
        except Exception as error:
            print(f"{n}x{n} #{name}: lỗi {error}", flush=True)
            runs.append(dict(n=n, name=name, status="error", seconds=None, cell=None, ids=[g["image_id"] for g in chunk], items={}, raw=str(error)))
    (m.OUT/"runs_clean_grid.json").write_text(json.dumps(dict(groups=chosen, runs=runs), ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["", "## Gemini, mỗi request một hội thoại mới: 2×2 / 3×3 / 4×4 (ảnh toàn khung, cùng 32 nhóm)", "",
             "| Lưới | Request | Request OK | Thời gian / request | **Thời gian / nhóm** | Ô trả đủ | Số người khớp gốc | Lệch ±1 trở xuống | Đếm thiếu / thừa | Cảnh báo màu khớp | OCR có/không khớp | Kích thước ô |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n in (2, 3, 4):
        rs = [r for r in runs if r["n"] == n]
        ok = [r for r in rs if r["status"] == "completed" and len(r["items"]) == len(r["ids"])]
        secs = [r["seconds"] for r in rs if r["seconds"]]
        got = {k: v for r in rs for k, v in r["items"].items()}
        pc = lambda k, v: (v.get("people_count"), base[k]["baseline"].get("people_count"))
        hit = sum(a == b for a, b in (pc(k, v) for k, v in got.items()))
        near = sum(isinstance(a, int) and isinstance(b, int) and abs(a-b) <= 1 for a, b in (pc(k, v) for k, v in got.items()))
        under = sum((a or 0) < (b or 0) for a, b in (pc(k, v) for k, v in got.items()))
        over = sum((a or 0) > (b or 0) for a, b in (pc(k, v) for k, v in got.items()))
        color = sum(bool(base[k]["baseline"].get("color_issues")) == bool(v.get("color_issues")) for k, v in got.items())
        ocr = sum(bool(base[k]["baseline"].get("ocr")) == bool(v.get("ocr")) for k, v in got.items())
        per = f"{sum(secs)/max(1, sum(len(r['ids']) for r in rs if r['seconds'])):.1f} s" if secs else "–"
        lines.append(f"| {n}×{n} | {len(rs)} | {len(ok)}/{len(rs)} | {statistics.mean(secs):.1f} s | **{per}** | {len(got)}/{GROUPS} | {hit}/{len(got)} | {near}/{len(got)} | {under} / {over} | {color}/{len(got)} | {ocr}/{len(got)} | {rs[0]['cell']} px |"
                     if secs else f"| {n}×{n} | {len(rs)} | 0/{len(rs)} | – | – | 0/{GROUPS} | – | – | – | – | – | – |")
    by = {n: {k: v for r in runs if r["n"] == n for k, v in r["items"].items()} for n in (2, 3, 4)}
    lines += ["", "| Ảnh | Người: gốc / 2×2 / 3×3 / 4×4 | Context 4×4 |", "|---|---|---|"]
    for k, g in base.items():
        lines.append(f"| {g['filename']} | {g['baseline'].get('people_count')} / " + " / ".join(str((by[n].get(k) or {}).get('people_count')) for n in (2, 3, 4))
                     + f" | {(by[4].get(k) or {}).get('context') or '—'} |")
    text = "\n".join(lines)+"\n"
    with open(m.OUT/"report.md", "a", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
