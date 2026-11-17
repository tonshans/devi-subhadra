"""Application-generated UTC timestamps: fixed width and strictly increasing.

SQLite has second precision by default and Postgres microseconds, and neither
guarantees insert order for rows created in the same tick. These strings give a
portable, lexicographically sortable ORDER BY key without a `rowid` column.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_last_micros = 0


def utc_now_text() -> str:
    global _last_micros
    with _lock:
        micros = max(time.time_ns() // 1000, _last_micros + 1)
        _last_micros = micros
    moment = datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f")
