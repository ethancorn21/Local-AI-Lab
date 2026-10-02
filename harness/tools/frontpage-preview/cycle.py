"""One fetch cycle for the frontpage preview, only when the newest item is over an hour old (run in a build dir)."""
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")
from frontpage.config import load_config  # noqa: E402
from frontpage.db import open_db, migrate  # noqa: E402
from frontpage.__main__ import seed_defaults  # noqa: E402
from frontpage.pipeline import run_cycle  # noqa: E402

time.sleep(20)   # let the server start (and migrate) first
cfg = load_config("config.yaml")
conn = open_db(f"{cfg.data_dir}/frontpage.db")
migrate(conn)
seed_defaults(conn, cfg)
try:
    newest = conn.execute("select max(fetched_at) from items").fetchone()[0]
except sqlite3.Error:
    newest = None
age_h = None
if newest:
    try:
        t = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).total_seconds() / 3600
    except ValueError:
        pass
if age_h is not None and age_h < 1:
    print(f"{datetime.now():%F %T} fetch skipped: newest item {age_h * 60:.0f} min old")
    sys.exit(0)
t0 = time.time()
stats = run_cycle(conn, cfg)
print(f"{datetime.now():%F %T} fetch cycle in {time.time() - t0:.0f} s: {stats}")
