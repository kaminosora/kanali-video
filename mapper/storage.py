"""Atomic JSON publication shared by the service and Blender's stdlib Python."""
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid


def write_json(path, value, *, retry_timeout=2.0, retry_interval=0.025):
    """Publish complete UTF-8 JSON, retrying brief rename sharing conflicts.

    Each writer owns a sibling temporary file. A reader that denies deletion
    (common on Windows) may briefly prevent replacement, so permission/sharing
    failures are retried for at most ``retry_timeout`` seconds. Other errors,
    including disk exhaustion, propagate immediately. Persistent failures also
    propagate, leaving the previously published document intact.
    """
    if not math.isfinite(retry_timeout) or retry_timeout < 0:
        raise ValueError('retry_timeout must be finite and nonnegative')
    if not math.isfinite(retry_interval) or retry_interval <= 0:
        raise ValueError('retry_interval must be finite and positive')
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(payload)
        deadline = time.monotonic() + retry_timeout
        while True:
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in (5, 32, 33)
                remaining = deadline - time.monotonic()
                if not transient or remaining <= 0:
                    raise
                time.sleep(min(retry_interval, remaining))
    finally:
        original_error = sys.exc_info()[1]
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if original_error is None:
                raise
            # Preserve the useful publication failure if even cleanup is denied.
            original_error.add_note(f'Could not remove JSON temporary file {temporary}: {cleanup_error}')
