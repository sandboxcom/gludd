#!/usr/bin/env python3
"""Parse a pytest --junit-xml file and print failures/errors + summary."""
import sys
import xml.etree.ElementTree as ET

xmlfile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/junit.xml"
try:
    tree = ET.parse(xmlfile)
except Exception as e:
    print(f"Cannot parse {xmlfile}: {e}")
    sys.exit(1)

root = tree.getroot()
suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
failures = []
total = errors = failed = skipped = passed = 0

for suite in suites:
    for tc in suite.findall("testcase"):
        total += 1
        cl = tc.get("classname", "")
        nm = tc.get("name", "")
        node = (cl + "::" + nm).lstrip("::")
        f = tc.find("failure")
        e = tc.find("error")
        s = tc.find("skipped")
        if f is not None:
            failed += 1
            msg = (f.get("message") or "").split("\n")[0][:120]
            failures.append(("FAILED", node, msg))
        elif e is not None:
            errors += 1
            msg = (e.get("message") or "").split("\n")[0][:120]
            failures.append(("ERROR", node, msg))
        elif s is not None:
            skipped += 1
        else:
            passed += 1

for kind, node, msg in failures:
    print(f"{kind} {node} | {msg}")

print()
print(f"SUMMARY: {total} tests — {passed} passed, {failed} failed, {errors} errors, {skipped} skipped")
