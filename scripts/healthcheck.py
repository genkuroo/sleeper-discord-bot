#!/usr/bin/env python3
"""Docker HEALTHCHECK: has the poller completed a cycle recently?

Exit 0 healthy, 1 unhealthy. The grace factor is generous on purpose — one
skipped cycle during a Sleeper blip is not a reason to restart a bot that is
otherwise connected.
"""

from __future__ import annotations

import os
import sys
import time

GRACE_FACTOR = 3

data_dir = os.environ.get("DATA_DIR", "/data")
poll_seconds = int(os.environ.get("POLL_SECONDS", "300"))
path = os.path.join(data_dir, "heartbeat")

try:
    age = time.time() - os.path.getmtime(path)
except OSError:
    # No heartbeat yet. Docker's start_period covers the first cycle, so
    # reaching here after that window means the poller never ran.
    print("no heartbeat file")
    sys.exit(1)

limit = poll_seconds * GRACE_FACTOR
if age > limit:
    print(f"stale heartbeat: {age:.0f}s old, limit {limit}s")
    sys.exit(1)

print(f"ok: heartbeat {age:.0f}s old")
sys.exit(0)
