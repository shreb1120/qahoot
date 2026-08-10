"""Shared performance aggregation.

The pass/critical predicates previously existed only inside dashboard_bp. The
agents list and the agent profile need identical semantics, and three copies of
an ILIKE expression drift — so they live here once.

`most_missed` is the coaching view: which requirements one agent actually fails.
Three things it has to get right, or the conversation it feeds is unfair:

* **Manager overrides win.** An item a manager already approved must not count
  against the agent. Raw model status is not the verdict; the reviewed status is.
* **`not_assessed` is not the agent's fault.** The grader returned no verdict for
  that requirement. It is reported separately and never folded into a miss rate
  used to coach someone.
* **A rate needs a denominator in front of the reader.** "100%" from a single
  call is a lie of omission, so occurrences travel with every rate.
"""
from __future__ import annotations

from sqlalchemy import case, func

from models import Agent, Call, Report

# One definition, shared.
#
# `verdict` is the indexed column the normalizer now writes. The ILIKE branch
# is the fallback for rows written before it existed — the migration backfills
# them, so in practice it never fires, but a report saved by an older process
# mid-deploy would otherwise be miscounted as a pass. Free-text matching is
# what we are moving away from; keeping it as a guarded fallback rather than
# deleting it means no call is ever silently scored wrong.
IS_PASS = case(
    (Report.verdict.is_not(None), case((Report.verdict == "pass", 1), else_=0)),
    (Report.pass_fail_status.ilike("%PASS%")
     & ~Report.pass_fail_status.ilike("%FAIL%"), 1),
    else_=0,
)
IS_CRITICAL = case(
    (Report.verdict.is_not(None), case((Report.verdict == "critical", 1), else_=0)),
    (Report.pass_fail_status.ilike("%CRITICAL%"), 1),
    else_=0,
)
IS_FAIL = case(
    (Report.verdict.is_not(None), case((Report.verdict == "fail", 1), else_=0)),
    (Report.pass_fail_status.ilike("%FAIL%")
     & ~Report.pass_fail_status.ilike("%CRITICAL%"), 1),
    else_=0,
)

# The three are evaluated in this order wherever a call is put in exactly one
# bucket, because the ILIKE fallback is not mutually exclusive: the pre-migration
# string "CRITICAL FAIL" matches both IS_CRITICAL and, but for its own guard,
# IS_FAIL. Critical first is the safe direction — a critical failure must never
# be reported as an ordinary one.
_BUCKETS = (("critical", IS_CRITICAL), ("pass", IS_PASS), ("fail", IS_FAIL))


def verdict_filter(result: str):
    """SQL criterion for the history page's result filter, or None.

    History used to fetch every row and filter with `"FAIL" in status` in
    Python. Same predicate, third copy, and it disagreed with this module:
    stats read `verdict` and history read the free-text string, so a report
    written before the normalizer counted one way on the dashboard and the
    other way in history.
    """
    for name, expr in _BUCKETS:
        if result == name:
            return expr == 1
    return None


def verdict_counts(query) -> dict:
    """{'passed': n, 'failed': n, 'critical': n, 'total': n} for a Call query.

    One aggregate over the whole filtered set. The counts stay honest across
    every page — a compliance tool that reported page-scoped totals would be
    worse than one that reported none.
    """
    # enable_eagerloads(False) matters: the caller's query carries
    # joinedload(Call.report) for rendering, and leaving it attached to an
    # aggregate adds a second, unjoined `reports` to the FROM clause — a
    # cartesian product that multiplies every count by the number of reports.
    row = query.enable_eagerloads(False).with_entities(
        func.count(Call.id),
        func.coalesce(func.sum(IS_PASS), 0),
        func.coalesce(func.sum(IS_FAIL), 0),
        func.coalesce(func.sum(IS_CRITICAL), 0),
    ).one()
    total, passed, failed, critical = row
    return {"total": total or 0, "passed": passed or 0,
            "failed": failed or 0, "critical": critical or 0}

# How many of an agent's calls the miss analysis reads. Headline counters come
# from SQL and stay exact; only this breakdown is capped.
MISS_SAMPLE_LIMIT = 500


def _rate(passed: int, graded: int) -> int | None:
    return round(passed / graded * 100) if graded else None


def agent_performance_rows(db, org_id: str, agent_id: str | None = None) -> list[dict]:
    """Per-agent pass rate. Scoped on BOTH Agent.org_id and Call.org_id.

    Reaching Calls through an Agent join means a call carrying another org's id
    would be counted if only the agent were scoped — the leak
    test_agent_performance_does_not_count_another_orgs_call exists for.
    """
    q = (
        db.query(
            Agent.id.label("id"),
            Agent.name.label("name"),
            func.count(Report.id).label("graded"),
            func.sum(IS_PASS).label("passed"),
            func.sum(IS_CRITICAL).label("critical"),
        )
        .join(Call, Call.agent_id == Agent.id)
        .join(Report, Report.call_id == Call.id)
        .filter(Agent.org_id == org_id, Call.org_id == org_id)
    )
    if agent_id:
        q = q.filter(Agent.id == agent_id)

    rows = [
        {
            "id": r.id,
            "name": r.name,
            "graded": r.graded,
            "passed": int(r.passed or 0),
            "critical": int(r.critical or 0),
            "pass_rate": _rate(int(r.passed or 0), r.graded),
        }
        for r in q.group_by(Agent.id, Agent.name).all()
    ]
    return sorted(rows, key=lambda a: (a["pass_rate"] if a["pass_rate"] is not None else 0,
                                       a["graded"]))


def section_key_of(section: dict) -> str:
    """Same derivation the normalizer uses, so keys line up with overrides."""
    from report_normalizer import _section_key
    return _section_key(section)


def _checklist_keys(checklist: dict | None) -> set[tuple[str, str]]:
    keys = set()
    for section in (checklist or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        key = section_key_of(section)
        for item in section.get("items") or []:
            if isinstance(item, dict) and item.get("name"):
                keys.add((key, str(item["name"])))
    return keys


def most_missed(calls, active_checklist: dict | None = None, min_occurrences: int = 2) -> dict:
    """Per-requirement miss rates for a set of calls.

    Returns {"ranked": [...], "seen_once": [...], "retired": [...]}. Each entry
    carries occurrences and missed so a rate is never shown bare.
    """
    tally: dict[tuple[str, str], dict] = {}

    for call in calls:
        report = getattr(call, "report", None)
        data = getattr(report, "report_json", None)
        if not isinstance(data, dict):
            continue
        overrides = getattr(report, "overrides_json", None) or {}

        for section in data.get("sections") or []:
            if not isinstance(section, dict):
                continue
            skey = section_key_of(section)
            sname = section.get("name") or skey
            for item in section.get("items") or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                # Absent `required` defaults to True, matching the normalizer and
                # any report written before it existed.
                if not item.get("required", True):
                    continue

                name = str(item["name"])
                entry = tally.setdefault(
                    (skey, name),
                    {"section": sname, "section_key": skey, "name": name,
                     "occurrences": 0, "missed": 0, "not_assessed": 0},
                )
                entry["occurrences"] += 1

                override = overrides.get(f"{skey}::{name}")
                if override == "approved":
                    continue                      # a manager cleared it
                if override == "failed":
                    entry["missed"] += 1
                    continue

                status = item.get("status")
                if status == "not_assessed":
                    # The grader gave no verdict. Not the agent's failing.
                    entry["not_assessed"] += 1
                elif status != "covered":
                    entry["missed"] += 1

    current = _checklist_keys(active_checklist)
    ranked, seen_once, retired = [], [], []

    for key, e in tally.items():
        e = dict(e)
        e["rate"] = round(e["missed"] / e["occurrences"] * 100) if e["occurrences"] else 0
        if current and key not in current:
            retired.append(e)
        elif e["occurrences"] < min_occurrences:
            seen_once.append(e)
        else:
            ranked.append(e)

    order = lambda e: (-e["rate"], -e["missed"], e["name"])
    return {
        "ranked": sorted([e for e in ranked if e["missed"]], key=order),
        "clean": sorted([e for e in ranked if not e["missed"]], key=lambda e: e["name"]),
        "seen_once": sorted([e for e in seen_once if e["missed"]], key=order),
        "retired": sorted([e for e in retired if e["missed"]], key=order),
    }


def call_score(report_json: dict | None) -> int | None:
    """The same covered/total the report page shows, so one call never displays
    two different numbers on two pages."""
    if not isinstance(report_json, dict):
        return None
    covered = total = 0
    for s in report_json.get("sections") or []:
        if isinstance(s, dict):
            covered += s.get("covered_count") or 0
            total += s.get("total_count") or 0
    return round(covered / total * 100) if total else None
