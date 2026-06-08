import sqlite3
import pathlib
import json
import sys

db = pathlib.Path.home() / ".local/share/opencode/opencode.db"
conn = sqlite3.connect(str(db))

project_dir = "/Users/shawnwilson/gludd"
sessions = conn.execute(
    "SELECT id FROM session WHERE directory = ? ORDER BY time_created",
    (project_dir,),
).fetchall()
session_ids = [s[0] for s in sessions]

print(f"Found {len(session_ids)} sessions for {project_dir}\n")

for sid in session_ids:
    msgs = conn.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
        (sid,),
    ).fetchall()

    user_msg_ids = []
    for msg in msgs:
        try:
            data = json.loads(msg[1])
            if data.get("role") == "user":
                user_msg_ids.append(msg[0])
        except (json.JSONDecodeError, TypeError):
            pass

    if not user_msg_ids:
        continue

    texts = []
    for mid in user_msg_ids:
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
            (mid,),
        ).fetchall()
        for p in parts:
            try:
                data = json.loads(p[0])
                if data.get("type") == "text" and data.get("text", "").strip():
                    texts.append(data["text"].strip())
            except (json.JSONDecodeError, TypeError):
                pass

    if texts:
        print(f"=== Session {sid[:12]}... ===")
        for t in texts:
            print(t[:800])
            print()
        print("---\n")

conn.close()
