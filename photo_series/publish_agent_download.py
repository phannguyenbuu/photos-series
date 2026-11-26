"""Run on the VPS to atomically publish a verified PrintAgent release."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sys

source = Path('/tmp/printagent-photo-series-next.exe')
with source.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
if actual != sys.argv[1] or source.read_bytes()[:2] != b'MZ':
    raise RuntimeError('Agent release checksum or format is invalid')
target = Path('/opt/printagent/static/releases/printagent.exe')
backup = Path('/var/backups')/('download-goxprint-agent-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
shutil.copy2(target, backup/'printagent.exe')
candidate = target.with_name('.printagent-next')
shutil.copy2(source, candidate)
candidate.chmod(0o755)
os.replace(candidate, target)
# Installers compare the download with this before replacing a working agent.
checksum = target.with_name('printagent.exe.sha256')
if checksum.exists():
    shutil.copy2(checksum, backup/checksum.name)
pending = checksum.with_name('.printagent.exe.sha256-next')
pending.write_text(f"{actual} {target.stat().st_size}
", encoding='ascii')
pending.chmod(0o644)
os.replace(pending, checksum)
print('Published latest PrintAgent; sha256:', actual, '; backup:', backup)
