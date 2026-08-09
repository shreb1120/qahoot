#!/usr/bin/env bash
# Nightly maintenance. Runs after the backup so anything deleted here is
# already in a dump.
#
#   45 3 * * * /srv/qaboom/deploy/nightly.sh >> /home/claude/backups/nightly.log 2>&1
set -euo pipefail
cd /srv/qaboom

# Audio past each org's retention window. Inert until an org sets one.
python3 scripts/retention.py

# Expired rate-limit windows. Nothing reads them once the window has passed.
python3 - <<'PY'
import os, sys
sys.path.insert(0, "/srv/qaboom")
from dotenv import load_dotenv; load_dotenv("/srv/qaboom/.env")
from db import init_db, get_db
init_db(os.environ["DATABASE_URL"])
import ratelimit
db = get_db()
try:
    print(f"purged {ratelimit.purge_old_windows(db)} expired rate-limit window(s)")
finally:
    db.close()
PY
