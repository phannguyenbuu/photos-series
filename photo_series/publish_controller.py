"""Publish the controller to DirToolx only; preserve previous hashed assets."""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

dist = Path(__file__).resolve().parent.parent.parent/'Goxprint/auto-gox-react/dist'
backup = '/var/backups/photo-series-controller-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
subprocess.run(['ssh', 'root@157.66.80.125', f'mkdir -p {backup}; cp /var/www/dir.toolxprint.com/html/index.html {backup}/index.html'], check=True)
assets = [str(path) for path in (dist/'assets').iterdir() if path.is_file()]
subprocess.run(['scp', *assets, 'root@157.66.80.125:/var/www/dir.toolxprint.com/html/assets/'], check=True)
subprocess.run(['scp', str(dist/'remoteEntry.js'), 'root@157.66.80.125:/var/www/dir.toolxprint.com/html/remoteEntry.js'], check=True)
subprocess.run(['scp', str(dist/'index.html'), 'root@157.66.80.125:/var/www/dir.toolxprint.com/html/index.html'], check=True)
print('Published controller to dir.toolxprint.com; backup:', backup)
