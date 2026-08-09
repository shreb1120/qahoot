#!/usr/bin/env python3
"""Delete audio past each organization's retention window.

**Ships inert.** `organizations.retention_days` is NULL everywhere, which means
keep indefinitely, and this job deletes nothing until someone sets a number.
That default is deliberate: deleting a recording a compliance report cites is
not recoverable, and the right window is a conversation with a customer, not a
value picked by whoever wrote the job.

What it removes is the **audio file only**. The call, its transcript, its
report and its usage row all survive — a report keeps its evidence text and
loses only playback. Usage rows in particular are never touched; they are the
evidence behind an invoice.

    python3 scripts/retention.py --dry-run
    python3 scripts/retention.py
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from db import get_db, init_db          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be deleted, delete nothing")
    args = ap.parse_args()

    init_db(os.environ["DATABASE_URL"])
    from models import Call, Organization

    db = get_db()
    try:
        orgs = db.query(Organization).filter(
            Organization.retention_days.is_not(None),
            Organization.retention_days > 0,
        ).all()

        if not orgs:
            print("No organization has a retention window set — nothing to do.")
            print("Audio is kept indefinitely until retention_days is set.")
            return 0

        freed = removed = 0
        for org in orgs:
            cutoff = datetime.now(timezone.utc) - timedelta(days=org.retention_days)
            calls = (
                db.query(Call)
                .filter(Call.org_id == org.id,
                        Call.upload_date < cutoff,
                        Call.audio_file_url.is_not(None))
                .all()
            )
            if not calls:
                continue

            print(f"\n{org.name} — keeping {org.retention_days} days, "
                  f"{len(calls)} call(s) past that")
            for call in calls:
                path = call.audio_file_url
                size = 0
                try:
                    size = os.path.getsize(path)
                except OSError:
                    # Already gone from disk. Still null the pointer so the app
                    # stops offering a play button for a file that isn't there.
                    pass

                print(f"  {call.upload_date:%Y-%m-%d}  {size / 1e6:>6.1f} MB  "
                      f"{os.path.basename(path)}")
                if not args.dry_run:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    # The row stays. Only playback is lost; the report keeps
                    # its evidence text, and the usage ledger is untouched.
                    call.audio_file_url = None
                freed += size
                removed += 1

        if args.dry_run:
            db.rollback()
            print(f"\nDRY RUN — would remove {removed} file(s), {freed / 1e6:.1f} MB")
        else:
            db.commit()
            print(f"\nRemoved {removed} file(s), freed {freed / 1e6:.1f} MB")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
