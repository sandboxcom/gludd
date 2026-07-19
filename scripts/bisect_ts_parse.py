#!/usr/bin/env python3
"""Find the exact TypeScript construct breaking --experimental-strip-types by REMOVING chunks."""
import subprocess, tempfile, os

FILE = ".opencode/plugin/enforce-stop.ts"

with open(FILE) as f:
    original = f.read()
    lines = original.split("\n")

total = len(lines)

def test_import(code):
    """Try to import code as TS. Returns (ok, first_error_line)."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ts', delete=False) as tf:
        tf.write(code)
        tf.flush()
        tpath = tf.name
    loader = f"import('{os.path.abspath(tpath)}').then(() => console.log('OK')).catch(e => console.error('ERR', e.message))"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mjs', delete=False) as tf2:
        tf2.write(loader)
        tf2.flush()
        lpath = tf2.name
    try:
        r = subprocess.run(
            ["node", "--experimental-strip-types", lpath],
            capture_output=True, text=True, timeout=30
        )
        ok = "OK" in r.stdout and "ERR" not in r.stdout
        return ok
    finally:
        try: os.unlink(tpath); os.unlink(lpath)
        except: pass

def test_strip(start_line, end_line):
    """Test with lines [start_line:end_line] removed. Returns ok."""
    code = "\n".join(lines[:start_line]) + "\n" + "\n".join(lines[end_line:])
    return test_import(code)

# Strategy: find which chunks can be removed to make it pass
# First, verify full file fails
print("Full file:", "PASS" if test_import(original) else "FAIL")

# Test removing large chunks
chunk_size = 100
failing_chunks = []
for start in range(0, total, chunk_size):
    end = min(start + chunk_size, total)
    ok = test_strip(start, end)
    if ok:
        print(f"  PASS when removing [{start}:{end}]")
    failing_chunks.append((start, end, ok))

# Find which chunks make it pass or fail
pass_chunks = [(s, e) for s, e, ok in failing_chunks if ok]
fail_chunks = [(s, e) for s, e, ok in failing_chunks if not ok]

print(f"\nPASS chunks: {len(pass_chunks)}, FAIL chunks: {len(fail_chunks)}")
for s, e in pass_chunks:
    print(f"  Removing [{s}:{e}] makes it PASS")
    
# If there's a chunk that fixes it when removed, focus there
if pass_chunks:
    # Test finer-grained in the first passing chunk
    s, e = pass_chunks[0]
    # Try removing the first half vs second half
    mid = (s + e) // 2
    for test_s, test_e in [(s, mid), (mid, e)]:
        if test_strip(test_s, test_e):
            print(f"  Sub-chunk [{test_s}:{test_e}] removal -> PASS")
            # Even finer
            for fine_s in range(test_s, test_e, 10):
                fine_e = min(fine_s + 10, test_e)
                if test_strip(fine_s, fine_e):
                    print(f"    Fine chunk [{fine_s}:{fine_e}] removal -> PASS")
                    # Show the removed lines
                    print(f"    REMOVED LINES:")
                    for i in range(fine_s, fine_e):
                        print(f"      {i}: {lines[i]}")
            break
    else:
        # Both halves fail individually — might be multi-line construct
        print(f"  Neither half [{s}:{mid}] nor [{mid}:{e}] fixes it alone")
