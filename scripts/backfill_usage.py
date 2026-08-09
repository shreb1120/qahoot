#!/usr/bin/env python3
"""Seed the usage ledger from calls that predate it.

Existing customers have history; without this their usage page reads zero on
the day metering ships, which looks like the feature is broken.

Only **transcription** events are reconstructed, from `calls.duration`. Analysis
events cannot be: token counts were never recorded, and inventing them would put
fiction in a ledger whose entire purpose is to be evidence. Their absence is
honest and visible.

Idempotent — the same unique keys the pipeline uses, so re-running adds nothing.

    python3 scripts/backfill_usage.py --dry-run
    python3 scripts/backfill_usage.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import usage                                            # noqa: E402
from db import get_db, init_db                          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, change nothing")
    args = ap.parse_args()

    init_db(os.environ["DATABASE_URL"])
    from models import Call, Organization, UsageEvent

    db = get_db()
    try:
        calls = (
            db.query(Call)
            .filter(Call.duration.is_not(None), Call.duration > 0)
            .order_by(Call.upload_date)
            .all()
        )
        print(f"{len(calls)} call(s) with a known duration")

        written = skipped = 0
        for call in calls:
            key = f"{call.id}:transcription:1"
            if db.query(UsageEvent).filter_by(idempotency_key=key).first():
                skipped += 1
                continue

            org = db.get(Organization, call.org_id)
            if org is None:                     # orphaned call, nothing to bill
                continue

            print(f"  {call.upload_date:%Y-%m-%d}  {org.name[:24]:24} "
                  f"{usage.minutes_from_seconds(call.duration):>4} min  {call.filename[:34]}")
            if not args.dry_run:
                # Attribute to the period the call actually happened in, not
                # today's — otherwise a year of history lands on this month's
                # invoice.
                usage.record(
                    db, org_id=org.id, call_id=call.id,
                    resource_type=usage.TRANSCRIPTION,
                    idempotency_key=key,
                    billable_seconds=int(call.duration),
                    meta={"source": "backfill", "note": "reconstructed from calls.duration"},
                    now=call.upload_date,
                )
            written += 1

        if args.dry_run:
            db.rollback()
            print(f"\nDRY RUN — would write {written}, skip {skipped} already present")
        else:
            db.commit()
            print(f"\nWrote {written}, skipped {skipped} already present")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
