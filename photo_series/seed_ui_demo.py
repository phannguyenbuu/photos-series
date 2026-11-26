"""Import the completed acceptance job into local PrintAgent for UI verification."""
import os
import shutil
from pathlib import Path
from .pipeline import Pipeline

source = Pipeline('output/photo-series-downloads-test')
destination = Pipeline(Path(os.environ['APPDATA'])/'GoxPrintAgent'/'photo-series')
job = source.get('downloads-remote-smoke')
if job['status'] != 'completed':
    raise RuntimeError('Real bridge test has not completed')
job['raw_files_skipped'] = 569
for group in job['series']:
    output = destination.directory/job['job_id']/group['series_id']
    output.mkdir(parents=True, exist_ok=True)
    for image in group['images']:
        if not image['selected']:
            continue
        for suffix in ('_thumbnail.webp', '_primary.webp', '_preview.jpg'):
            name = image['image_id']+suffix
            shutil.copy2(source.directory/job['job_id']/group['series_id']/name, output/name)
destination.save(job)
print('Imported completed 3-series acceptance job into local PrintAgent.')
