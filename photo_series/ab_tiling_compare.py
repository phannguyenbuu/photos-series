"""Compare A (single), B (2x2 crops) and B' (2x2 full frames) on the same groups; appends to report.md."""
import json, re, statistics, sys
from ab_tiling import OUT, parse

sys.stdout.reconfigure(encoding="utf-8")
first = json.loads((OUT/"runs.json").read_text(encoding="utf-8"))
full = json.loads((OUT/"runs_full.json").read_text(encoding="utf-8"))
groups = {g["image_id"]: g for g in first["groups"]}


def collect(runs, kind):
    items, times, fails = {}, [], 0
    for r in runs:
        if r["kind"] != kind:
            continue
        ok = r["status"] == "completed" and parse(r["result_text"]) is not None
        fails += not ok
        if ok:
            times.append(r["service_s"]/len(r["groups"]))
        f = r if ok else r.get("retry") or r
        parsed = parse(f.get("result_text") or "") or {}
        for i in parsed.get("images", []):
            if isinstance(i, dict) and i.get("image_id") in r["groups"]:
                items[i["image_id"]] = i | ({"context": parsed.get("context")} if kind == "single" else {})
    return items, times, fails


def words(t):
    return set(re.findall(r"\w+", (t or "").lower()))


variants = {"A: 1 ảnh/request": collect(first["runs"], "single"), "B: lưới crop 2/3": collect(first["runs"], "tile"),
            "B': lưới toàn khung": collect(full["runs"], "tile")}
lines = ["", "## Lượt B': lưới 2×2 ảnh toàn khung (cùng 20 nhóm)", "",
         "| | A: 1 ảnh/request | B: lưới crop 2/3 | B': lưới toàn khung |", "|---|---:|---:|---:|"]
def row(label, fn):
    lines.append("| "+label+" | "+" | ".join(fn(*v) for v in variants.values())+" |")
row("Trung vị thời gian / nhóm", lambda i, t, f: f"{statistics.median(t):.1f} s" if t else "–")
row("Request lỗi lần đầu", lambda i, t, f: str(f))
def match(field):
    def fn(items, t, f):
        hit = sum((groups[k]["baseline"].get(field) == v.get(field)) if field == "people_count" else (bool(groups[k]["baseline"].get(field)) == bool(v.get(field))) for k, v in items.items())
        return f"{hit}/{len(items)}"
    return fn
row("Số người khớp gốc", match("people_count"))
row("Đếm thiếu / đếm thừa", lambda i, t, f: f"{sum((v.get('people_count') or 0) < (groups[k]['baseline'].get('people_count') or 0) for k, v in i.items())} / {sum((v.get('people_count') or 0) > (groups[k]['baseline'].get('people_count') or 0) for k, v in i.items())}")
row("Có/không OCR khớp", match("ocr"))
row("Có/không cảnh báo màu khớp", match("color_issues"))
def jac(items, t, f):
    s = [len(words(groups[k]["baseline"]["series_context"]) & words(v.get("context")))/max(1, len(words(groups[k]["baseline"]["series_context"]) | words(v.get("context")))) for k, v in items.items()]
    return f"{statistics.mean(s):.2f}" if s else "–"
row("Độ trùng từ context (Jaccard TB)", jac)
lines += ["", "| Ảnh | Người: gốc / A / B / B' | Context B' |", "|---|---|---|"]
for k, g in groups.items():
    counts = " / ".join(str((v[0].get(k) or {}).get("people_count")) for v in variants.values())
    lines.append(f"| {g['filename']} | {g['baseline'].get('people_count')} / {counts} | {(variants[chr(66)+chr(39)+': lưới toàn khung'][0].get(k) or {}).get('context') or '—'} |")
text = "\n".join(lines)+"\n"
report = (OUT/"report.md").read_text(encoding="utf-8").split("\n## Lượt B'")[0].rstrip("\n")+"\n"
(OUT/"report.md").write_text(report+text, encoding="utf-8")
print(text)
