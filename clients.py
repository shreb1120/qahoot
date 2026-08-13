"""Every call with one client, rolled into one view.

A client is not a row in this database. They are the number that arrives in the
recording's filename, so grouping on it costs nothing and works backwards over
everything already uploaded — including calls uploaded weeks apart, which an
upload-time grouping could never join.

What the roll-up is for
-----------------------
A per-call verdict answers "was this call compliant". A reviewer coaching an
agent, or answering an auditor about a *customer*, is asking something else:
was this person told what they were owed, at any point, and by whom.

Those come apart constantly. A disclosure missed on Tuesday and made on
Wednesday is a different situation from one never made at all, and two separate
FAIL reports cannot say so. The matrix here shows a requirement across every
call the client had, in order, so the trajectory is visible — which is also the
only way to see that coaching worked.

It never re-grades. Each call keeps its own report, its own audio and its own
verdict; this sits above them and reads what is already there.
"""
from __future__ import annotations

from collections import OrderedDict

from models import Call, Report
from upload_meta import normalise_phone

# Statuses a requirement can hold on one call, worst first — the order the
# summary uses when deciding what a requirement's overall state is.
MISSED, NOT_ASSESSED, COVERED, ABSENT = "missed", "not_assessed", "covered", "absent"


def client_key(phone: str | None) -> str | None:
    """The grouping key: digits only, so formatting differences do not split a
    client in two. (555) 014-2200 and 5550142200 are one person."""
    return normalise_phone(phone)


def list_clients(db, org_id: str) -> list[dict]:
    """Every client with at least one graded call, most recent first."""
    rows = (
        db.query(Call)
        .join(Report, Report.call_id == Call.id)
        .filter(Call.org_id == org_id, Call.client_phone.isnot(None))
        .order_by(Call.call_date.desc().nullslast(), Call.upload_date.desc())
        .all()
    )
    out: OrderedDict[str, dict] = OrderedDict()
    for call in rows:
        key = client_key(call.client_phone)
        if not key:
            continue
        entry = out.setdefault(key, {
            "key": key, "phone": call.client_phone, "name": None, "calls": 0,
            "seconds": 0, "verdicts": [], "agents": set(), "latest": None,
        })
        entry["calls"] += 1
        entry["seconds"] += call.duration or 0
        entry["verdicts"].append(call.report.verdict)
        # Most recent non-empty name wins: the query is newest-first, and a
        # later spelling is the one the reviewer used most recently.
        if call.client_name and not entry["name"]:
            entry["name"] = call.client_name
        if call.agent:
            entry["agents"].add(call.agent.name)
        entry["latest"] = entry["latest"] or (call.call_date or call.upload_date.date())
    for e in out.values():
        e["agents"] = sorted(e["agents"])
    return list(out.values())


def _effective(item: dict, overrides: dict, section_key: str) -> str:
    """A requirement's status after any manager override.

    Overrides are the reviewer's correction of the grader, and they are what the
    scorecard counts — so a roll-up that ignored them would contradict the
    agent's own numbers.
    """
    key = f"{section_key}::{item.get('name', '')}"
    ov = (overrides or {}).get(key)
    if ov == "approved":
        return COVERED
    if ov == "failed":
        return MISSED
    status = item.get("status")
    if status == COVERED:
        return COVERED
    if status == NOT_ASSESSED:
        return NOT_ASSESSED
    return MISSED


def roll_up(db, org_id: str, key: str) -> dict | None:
    """The matrix for one client: every requirement against every call.

    Returns None when the org has no graded calls for that number — which is
    also what stops one org reading another's client, since org_id is part of
    the query rather than a check applied afterwards.
    """
    from report_normalizer import _section_key

    calls = [
        c for c in (
            db.query(Call)
            .join(Report, Report.call_id == Call.id)
            .filter(Call.org_id == org_id, Call.client_phone.isnot(None))
            .order_by(Call.call_date.asc().nullsfirst(), Call.upload_date.asc())
            .all()
        )
        if client_key(c.client_phone) == key
    ]
    if not calls:
        return None

    # Requirements are collected in the order the first call presents them, so
    # the matrix reads like the checklist rather than like a set.
    rows: OrderedDict[tuple, dict] = OrderedDict()
    for idx, call in enumerate(calls):
        data = call.report.report_json or {}
        overrides = call.report.overrides_json or {}
        for section in data.get("sections") or []:
            if not isinstance(section, dict):
                continue
            skey = _section_key(section)
            sname = section.get("name") or skey
            for item in section.get("items") or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                if not item.get("required", True):
                    continue
                rk = (sname, item["name"])
                row = rows.setdefault(rk, {
                    "section": sname, "name": item["name"],
                    "cells": [{"status": ABSENT, "timestamp": None}] * 0,
                })
                while len(row["cells"]) < idx:
                    row["cells"].append({"status": ABSENT, "timestamp": None})
                row["cells"].append({
                    "status": _effective(item, overrides, skey),
                    "timestamp": item.get("timestamp"),
                })
    for row in rows.values():
        while len(row["cells"]) < len(calls):
            row["cells"].append({"status": ABSENT, "timestamp": None})
        seen = [c["status"] for c in row["cells"] if c["status"] != ABSENT]
        row["ever_covered"] = COVERED in seen
        row["ever_missed"] = MISSED in seen
        # Missed, then covered on a later call. The single most useful thing on
        # this page: it is the shape of a problem that got fixed, and neither
        # call's own report can show it.
        row["fixed"] = (
            MISSED in seen and COVERED in seen
            and seen.index(MISSED) < len(seen) - 1 - seen[::-1].index(COVERED)
        )
        row["outstanding"] = row["ever_missed"] and not row["fixed"]

    phrases = []
    for idx, call in enumerate(calls):
        af = (call.report.report_json or {}).get("auto_fail_phrases") or {}
        for p in af.get("phrases") or []:
            if p.get("spoken_by") == "client" or not p.get("is_violation", True):
                continue
            phrases.append({"call_index": idx, "call": call, **p})

    flips = [
        {"call_index": i, "call": c,
         **((c.report.report_json or {}).get("program_flip") or {})}
        for i, c in enumerate(calls)
        if ((c.report.report_json or {}).get("program_flip") or {}).get("detected")
    ]

    rows_list = list(rows.values())
    # calls is oldest-first here, so take the last non-empty name.
    name = next((c.client_name for c in reversed(calls) if c.client_name), None)

    return {
        "key": key,
        "phone": calls[0].client_phone,
        "name": name,
        "calls": calls,
        "rows": rows_list,
        "total_seconds": sum(c.duration or 0 for c in calls),
        "outstanding": [r for r in rows_list if r["outstanding"]],
        "fixed": [r for r in rows_list if r["fixed"]],
        "agent_phrases": phrases,
        "program_flips": flips,
    }
