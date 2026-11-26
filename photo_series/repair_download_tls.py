"""Repair migrated Certbot live links on the VPS, preserving all old certificate files."""
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil

domain = 'download.goxprint.com'
live = Path('/etc/letsencrypt/live')/domain
archive = Path('/etc/letsencrypt/archive')/domain
names = ('cert', 'chain', 'fullchain', 'privkey')
for name in names:
    if (live/(name+'.pem')).read_bytes() != (archive/(name+'1.pem')).read_bytes():
        raise RuntimeError('Archive differs from live certificate; manual review needed')
backup = Path('/var/backups')/('download-goxprint-tls-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
for name in names:
    current = live/(name+'.pem')
    shutil.copy2(current, backup/current.name)
    link = live/(name+'.photo-deploy-link')
    link.symlink_to('../../archive/'+domain+'/'+name+'1.pem')
    os.replace(link, current)
print('Restored Certbot live symlinks; original files backed up:', backup)
