"""Summarise output/photo-series-ab-tiling/runs.json: speed per group and agreement with the baseline."""
import json, re, statistics, sys
from pathlib import Path
from ab_tiling import OUT, parse

sys.stdout.reconfigure(encoding="utf-8")
data = json.loads((OUT/"runs.json").read_text(encoding="utf-8"))
groups = {g["image_id"]: g for g in data["groups"]}


def final(r):
    return r.get("retry") if r.get("retry") and (r["status"] != "completed" or not parse(r["result_text"])) else r


def words(text):
    return set(re.findall(r"\w+", (text or "").lower()))


def jaccard(a, b):
    a, b = words(a), words(b)
    return len(a & b)/len(a | b) if a | b else 1.0


rows, timing = {}, {"single": [], "tile": []}
problems = []
for r in data["runs"]:
    first_ok = r["status"] == "completed" and parse(r["result_text"]) is not None
    f = final(r)
    parsed = parse(f["result_text"]) if f["status"] == "completed" else None
    if first_ok:
        timing[r["kind"]].append(r["service_s"]/len(r["groups"]))
    else:
        problems.append(f"{r['task']}: lần 1 {r['status']}{' → thử lại '+f['status'] if r.get('retry') else ''}")
    items = {i.get("image_id"): i for i in (parsed or {}).get("images", []) if isinstance(i, dict)}
    if r["kind"] == "tile":
        if parsed and set(items) != set(r["groups"]):
            problems.append(f"{r['task']}: image_id trả về không khớp bảng ô ({len(items)}/{len(r['groups'])})")
        for n, image_id in enumerate(r["groups"], 1):
            item = items.get(image_id)
            if item and item.get("tile") not in (n, str(n)):
                problems.append(f"{r['task']}: ô {n} ghi tile={item.get('tile')}")
    for image_id in r["groups"]:
        item = items.get(image_id)
        context = (item or {}).get("context") if r["kind"] == "tile" else (parsed or {}).get("context")
        rows.setdefault(image_id, {})[r["kind"]] = dict(item=item, context=context)


def compare(kind, field):
    hits = total = 0
    for image_id, row in rows.items():
        base = groups[image_id]["baseline"]; got = (row.get(kind) or {}).get("item")
        if not got:
            continue
        total += 1
        if field == "people_count":
            hits += base.get("people_count") == got.get("people_count")
        else:
            hits += bool(base.get(field)) == bool(got.get(field))
    return f"{hits}/{total}"


lines = ["# A/B: 1 ảnh mỗi request vs lưới 2×2", "",
         f"- Job nguồn: `{data['job']}`, {len(groups)} nhóm (seed {data['seed']}), worker: {', '.join(data['workers'])}",
         f"- Thời gian toàn bộ lượt test (A và B chạy xen kẽ): {data['wall_seconds']} s", "",
         "## Tốc độ (thời gian worker xử lý, không gồm xếp hàng; chỉ tính lần chạy đầu thành công)", "",
         "| Cách gửi | Request OK | Trung vị / nhóm | Trung bình / nhóm |", "|---|---:|---:|---:|"]
for kind, label in (("single", "A: 1 ảnh/request"), ("tile", "B: lưới 2×2")):
    t = timing[kind]; n = sum(r["kind"] == kind for r in data["runs"])
    ok = len(t)
    lines.append(f"| {label} | {ok}/{n} | {statistics.median(t):.1f} s | {statistics.mean(t):.1f} s |" if t else f"| {label} | 0/{n} | – | – |")
if timing["single"] and timing["tile"]:
    lines += ["", f"**B nhanh hơn A khoảng {statistics.median(timing['single'])/statistics.median(timing['tile']):.2f} lần mỗi nhóm (theo trung vị).**"]
lines += ["", "## Khớp với kết quả gốc của job (gửi 1 ảnh trước đây)", "",
          "| Trường | A (gửi lại 1 ảnh) | B (lưới) |", "|---|---:|---:|"]
for field, label in (("people_count", "Số người trùng khớp"), ("ocr", "Có/không có chữ OCR trùng"), ("color_issues", "Có/không cảnh báo màu trùng")):
    lines.append(f"| {label} | {compare('single', field)} | {compare('tile', field)} |")
sim = {k: [jaccard(groups[i]["baseline"].get("series_context"), (row.get(k) or {}).get("context")) for i, row in rows.items() if (row.get(k) or {}).get("context")] for k in ("single", "tile")}
lines.append(f"| Độ trùng từ của context (Jaccard TB) | {statistics.mean(sim['single']):.2f} | {statistics.mean(sim['tile']):.2f} |" if sim["single"] and sim["tile"] else "| Context | – | – |")
lines += ["", "Mức trùng giữa A và kết quả gốc cho thấy độ dao động tự nhiên khi hỏi lại cùng một ảnh; B nên được so với mức đó, không phải với 100%.", "",
          "## Sự cố", ""] + ([f"- {p}" for p in problems] or ["- Không có"])
lines += ["", "## Đối chiếu từng nhóm", "", "| Ảnh | Gốc | A | B |", "|---|---|---|---|"]
for image_id, row in rows.items():
    g = groups[image_id]; b = g["baseline"]
    cell = lambda k: f"{(row.get(k) or {}).get('context') or '—'} · người: {((row.get(k) or {}).get('item') or {}).get('people_count')}"
    lines.append(f"| {g['filename']} | {b.get('series_context')} · người: {b.get('people_count')} | {cell('single')} | {cell('tile')} |")
(OUT/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
print("\n".join(lines[:30]))
