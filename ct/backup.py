"""Backup rotation for save files.

Uses filesystem modification times instead of parsing timestamps from
filenames, so it won't explode if a file doesn't match the naming convention.
"""

import os
import shutil
from datetime import datetime


def create_backup(file_path, backup_dir, limit=5):
    """Create a timestamped backup of file_path, rotating oldest past limit."""
    if not os.path.exists(file_path):
        return

    os.makedirs(backup_dir, exist_ok=True)

    name, ext = os.path.splitext(os.path.basename(file_path))

    # Gather existing backups, sorted oldest-first by actual modification time
    backups = sorted(
        (
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith(f"{name}_") and f.endswith(ext)
        ),
        key=os.path.getmtime,
    )

    while len(backups) >= limit:
        os.remove(backups.pop(0))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shutil.copy2(file_path, os.path.join(backup_dir, f"{name}_{timestamp}{ext}"))
