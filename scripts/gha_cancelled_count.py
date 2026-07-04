#!/usr/bin/env python3
"""Count cancelled CI runs in the last 2 hours for the master branch."""

import json
import subprocess
from datetime import datetime, timedelta, timezone

try:
    result = subprocess.run(
        ["gh", "run", "list", "--branch=master", "--limit=20",
         "--json", "status,createdAt"],
        capture_output=True, text=True, timeout=15,
    )
    runs = json.loads(result.stdout)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    cancelled = 0
    for run in runs:
        created = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if created > cutoff and run["status"].lower() in ("cancelled",):
            cancelled += 1
    print(cancelled)
except Exception:
    print(0)
