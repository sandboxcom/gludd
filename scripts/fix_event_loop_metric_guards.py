#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

PATH = Path("src/general_ludd/event_loop/loop.py")
text = PATH.read_text()
text = re.sub(
    r"\n(?P<indent>[ \t]*)if phase_name != \"emit_tick_metrics\":\n[ \t]*(?P<metric>self\._tick_metrics(?:\[|:))",
    r"\n\g<indent>\g<metric>",
    text,
)
needle = "                await phase_fn()\n                logger.info(\"Phase completed: %s\", phase_name)\n                self._tick_metrics[\"phases_completed\"] += 1"
replacement = "                await phase_fn()\n                logger.info(\"Phase completed: %s\", phase_name)\n                if phase_name != \"emit_tick_metrics\":\n                    self._tick_metrics[\"phases_completed\"] += 1"
if needle in text:
    text = text.replace(needle, replacement, 1)
PATH.write_text(text)
