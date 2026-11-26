"""Publish only assets referenced by the newly built HTML, preserving old bundles."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent / 'GenImgs' / 'dist'
document = (root/'index.html').read_text(encoding='utf-8')
assets = re.findall(r'(?:src|href)="(/assets/[^"\s]+)"', document)
for asset in assets:
    source = root / asset.lstrip('/')
    if not source.is_file():
        raise RuntimeError(f'Built asset missing: {source}')
    subprocess.run(['scp', str(source), 'root@157.66.80.125:/var/www/genai.toolxprint.com/dist/assets/'], check=True)
subprocess.run(['scp', str(root/'index.html'), 'root@157.66.80.125:/var/www/genai.toolxprint.com/dist/index.html'], check=True)
print('Published UI HTML and referenced bundles.')
