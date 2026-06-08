#!/usr/bin/env python3
import json
import sys

with open("/Users/shawnwilson/.local/share/opencode/tool-output/tool_ea4b4a35b001WxQ34qm9c8wiD9") as f:
    data = json.load(f)

values = set()
for m in data["data"]:
    arch = m.get("architecture", {})
    val = arch.get("instruct_type")
    values.add(val)

for v in sorted(values, key=lambda x: (x is None, str(x))):
    print(repr(v))
print(f"\nTotal unique values: {len(values)}")
print(f"Total models: {len(data['data'])}")
