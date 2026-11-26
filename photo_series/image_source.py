"""Read camera files with CPU image libraries; prefer camera-rendered RAW previews."""
import io
import hashlib
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageOps
import exifread
import rawpy

RAW_EXTENSIONS = {'.raw', '.arw', '.cr2', '.cr3', '.nef', '.dng', '.raf', '.orf', '.rw2', '.pef', '.srw'}


def read_image(path, max_dimension=None):
    path = Path(path)
    if path.suffix.lower() not in RAW_EXTENSIONS:
        with Image.open(path) as source:
            exif = source.getexif()
            details = exif.get_ifd(34665) if 34665 in exif else {}
            date = details.get(36867) or exif.get(36867) or exif.get(306)
            subsecond = str(details.get(37521) or exif.get(37521, '0'))
            offset = str(details.get(36881) or exif.get(36881) or '')
            orientation = int(exif.get(274, 1))
            dimensions = source.size[::-1] if orientation in (5, 6, 7, 8) else source.size
            camera = '|'.join(str(exif.get(key, '')) for key in (271, 272))
            if max_dimension:
                source.draft('RGB', (max_dimension, max_dimension))
            image = ImageOps.exif_transpose(source).convert('RGB')
        method = 'image'
    else:
        with path.open('rb') as stream:
            tags = exifread.process_file(stream, details=False)
        date = str(tags.get('EXIF DateTimeOriginal') or tags.get('Image DateTime') or '')
        subsecond = str(tags.get('EXIF SubSecTimeOriginal') or '0')
        offset = str(tags.get('EXIF OffsetTimeOriginal') or '')
        camera = '|'.join(str(tags.get(key) or '') for key in ('Image Make', 'Image Model'))
        orientation_tag = tags.get('Image Orientation')
        orientation = int(orientation_tag.values[0]) if orientation_tag else 1
        with rawpy.imread(str(path)) as raw:
            width, height = raw.sizes.width, raw.sizes.height
            dimensions = (height, width) if orientation in (5, 6, 7, 8) else (width, height)
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(thumb.data)) as source:
                        image = source.convert('RGB')
                    method = 'raw_embedded_preview'
                else:
                    image = Image.fromarray(thumb.data).convert('RGB')
                    method = 'raw_embedded_bitmap'
                turns = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                         4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                         6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90}
                if orientation in turns:
                    image = image.transpose(turns[orientation])
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                image = Image.fromarray(raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)).convert('RGB')
                method = 'raw_cpu_develop'
    try:
        capture_date = datetime.strptime(str(date), '%Y:%m:%d %H:%M:%S')
        if offset and len(offset) == 6 and offset[0] in '+-':
            capture_date = datetime.fromisoformat(capture_date.isoformat()+offset)
        captured = capture_date.timestamp() + float('0.'+subsecond)
    except (ValueError, TypeError):
        captured = None
    if max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return image, dict(original_width=dimensions[0], original_height=dimensions[1], captured_at=captured,
                       time_source='exif' if captured is not None else 'mtime', decode_method=method,
                       capture_time_original=str(date or ''), subsecond_original=subsecond, utc_offset=offset,
                       camera_signature=hashlib.sha256(camera.encode()).hexdigest()[:16] if camera.strip('|') else None)
