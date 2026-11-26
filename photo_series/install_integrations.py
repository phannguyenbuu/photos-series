"""Apply reviewed source integration to sibling PrintAgent and GenAI projects."""
from pathlib import Path
import shutil

SOURCE = Path(__file__).parent
ROOT = SOURCE.parent.parent
AGENT = ROOT / "Goxprint" / "GoxAgent"
GENAI = ROOT / "GenImgs"


def modify(path, old, new):
    text = path.read_text(encoding="utf-8-sig")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Integration anchor ambiguous: {path}")
    backup = path.with_name(path.name+".before-photo-series")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text.replace(old, new), encoding="utf-8")


def install():
    # Verify all anchors before making mutations.
    patches = [
        (AGENT/"agent/webs/web.py", 'if request.method == "OPTIONS" and not request.path.startswith(("/retouch-api/", "/rapidretouch/")):',
         'if request.method == "OPTIONS" and not request.path.startswith(("/retouch-api/", "/rapidretouch/", "/api/photo-series")):'),
        (AGENT/"agent/webs/web.py", 'if request.path.startswith(("/retouch-api/", "/rapidretouch/")):',
         'if request.path.startswith(("/retouch-api/", "/rapidretouch/", "/api/photo-series")):'),
        (AGENT/"agent/webs/web.py", "    register_retouch_routes(app)",
         "    register_retouch_routes(app)\n    from agent.photo_series.local_routes import register_photo_series_routes\n    register_photo_series_routes(app)"),
        (GENAI/"vps_backend/server.py", "orchestrator = Orchestrator()",
         "orchestrator = Orchestrator()\nfrom photo_series.bridge_routes import register_photo_series_bridge\nregister_photo_series_bridge(app, orchestrator, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'photo-series-state'))"),
    ]
    for path, old, new in patches:
        text = path.read_text(encoding="utf-8-sig")
        if new not in text and text.count(old) != 1:
            raise RuntimeError(f"Integration anchor ambiguous: {path}")
    for destination in (AGENT/"agent/photo_series", GENAI/"vps_backend/photo_series"):
        destination.mkdir(exist_ok=True)
        for name in ("__init__.py", "pipeline.py", "image_source.py", "grouping.py", "local_routes.py", "bridge_routes.py", "grid_batch.py", "export.py", "people_split.py", "config.json"):
            shutil.copy2(SOURCE/name, destination/name)
    for path, old, new in patches:
        modify(path, old, new)
    requirements = AGENT/"agent/requirements.txt"
    existing = requirements.read_text(encoding="utf-8")
    additions = [line for line in (SOURCE/"requirements.txt").read_text().splitlines() if line.split(">=")[0].lower() not in existing.lower()]
    if additions:
        requirements.write_text(existing.rstrip()+"\n"+"\n".join(additions)+"\n", encoding="utf-8")
    from install_controller import install_controller
    install_controller()
    print("Integrated PrintAgent routes, GenAI bridge and DirToolx controller.")


if __name__ == "__main__":
    install()
