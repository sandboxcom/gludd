import sqlite3
import pathlib
import json

db = pathlib.Path.home() / ".local/share/opencode/opencode.db"
conn = sqlite3.connect(str(db))

project_dir = "/Users/shawnwilson/gludd"
sessions = conn.execute(
    "SELECT id FROM session WHERE directory = ? ORDER BY time_created DESC LIMIT 3",
    (project_dir,),
).fetchall()

for sid in sessions:
    print(f"\n=== Session {sid[0][:16]}... ===")
    msgs = conn.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created LIMIT 4",
        (sid[0],),
    ).fetchall()
    for m in msgs:
        print(f"\n  msg {m[0][:16]}...")
        try:
            data = json.loads(m[1])
            print(f"  keys: {list(data.keys())}")
            print(f"  role: {data.get('role')}")
            content = data.get("content")
            print(f"  content type: {type(content).__name__}")
            print(f"  content preview: {json.dumps(content, ensure_ascii=False)[:300] if content else 'None'}")
        except Exception as e:
            print(f"  parse error: {e}")
            print(f"  raw: {m[1][:300] if m[1] else 'None'}")

    parts = conn.execute(
        "SELECT id, data FROM part WHERE session_id = ? ORDER BY time_created LIMIT 4",
        (sid[0],),
    ).fetchall()
    print(f"\n  --- Parts for this session ({len(parts)} shown) ---")
    for p in parts:
        print(f"\n  part {p[0][:16]}...")
        try:
            data = json.loads(p[1])
            print(f"  keys: {list(data.keys())}")
            print(f"  preview: {json.dumps(data, ensure_ascii=False)[:300]}")
        except Exception as e:
            print(f"  parse error: {e}")
            print(f"  raw: {p[1][:300] if p[1] else 'None'}")

conn.close()
