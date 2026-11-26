"""Copy the best photos of every series into a new `selected` subfolder and tag the copies with the series context.

Originals are never touched. JPEG copies get the context in EXIF (ImageDescription, XPComment, UserComment) without
re-encoding; RAW and other copies stay byte-identical and get an XMP sidecar with the same text.
"""
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

import piexif
import piexif.helper

EXPORT_DIR = re.compile(r"selected(_\d+)?", re.IGNORECASE)
JPEG = {".jpg", ".jpeg"}


def is_export_dir(part):
    return bool(EXPORT_DIR.fullmatch(part))


def new_folder(root):
    for index in range(1, 1000):
        folder = root/("selected" if index == 1 else f"selected_{index}")
        if not folder.exists():
            folder.mkdir()
            return folder
    raise RuntimeError("Too many export folders")


def unique(folder, name, used):
    path, index = folder/name, 2
    while path.name.casefold() in used:
        path = folder/f"{Path(name).stem}_{index}{Path(name).suffix}"
        index += 1
    used.add(path.name.casefold())
    return path


def write_exif(path, text):
    exif = piexif.load(str(path))
    exif["0th"][piexif.ImageIFD.ImageDescription] = text.encode("utf-8")
    exif["0th"][piexif.ImageIFD.XPComment] = tuple(text.encode("utf-16le")+b"\0\0")
    exif["Exif"][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(text, encoding="unicode")
    try:
        data = piexif.dump(exif)
    except (ValueError, TypeError, KeyError):
        # Some camera maker notes and vendor tags do not round-trip; the description matters more than them.
        exif["Exif"].pop(piexif.ExifIFD.MakerNote, None)
        exif["Exif"].pop(piexif.ExifIFD.SceneType, None)
        exif.pop("thumbnail", None)
        exif["1st"] = {}
        data = piexif.dump(exif)
    piexif.insert(data, str(path))


def write_sidecar(path, text):
    body = escape(text)
    path.with_suffix(".xmp").write_text(
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{body}</rdf:li></rdf:Alt></dc:description>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>\n', encoding="utf-8")


def export_best(job, count, progress=None, check_cancel=None):
    if count not in (1, 2, 3):
        raise ValueError("count must be 1, 2 or 3")
    root = Path(job["folder"])
    folder = new_folder(root)
    plan = []
    for group in job.get("series", []):
        context = ((group.get("result") or {}).get("context") or "").strip()
        for image in sorted(group["images"], key=lambda item: item["rank"])[:count]:
            plan.append((image, context))
    stats = dict(folder=str(folder), count=count, images=len(plan), files=0, exif=0, sidecars=0, without_context=0, errors=[])
    used = set()
    for done, (image, context) in enumerate(plan, 1):
        if check_cancel:
            check_cancel()
        stats["without_context"] += not context
        for source in image.get("source_files") or [image["path"]]:
            source = Path(source)
            try:
                target = unique(folder, source.name, used)
                shutil.copy2(source, target)
                stats["files"] += 1
                if not context:
                    continue
                if target.suffix.lower() in JPEG:
                    write_exif(target, context)
                    stats["exif"] += 1
                else:
                    write_sidecar(target, context)
                    stats["sidecars"] += 1
            except Exception as error:  # one unreadable file must not stop the rest
                stats["errors"].append(dict(filename=source.name, error=type(error).__name__))
        if progress:
            progress(done, len(plan))
    return stats
