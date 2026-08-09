"""Reconcile a model response against the checklist that was in force.

The grading model is asked for a specific shape, and usually returns it. But
"usually" is not a property a compliance report can be built on. If a response
omits a required item, the old behaviour was to store what came back — so the
requirement simply vanished from the report, the score was computed from a
smaller denominator, and the reviewer had no way to know an obligation was never
assessed. That is the most dangerous failure this product can have, because it
looks exactly like a clean call.

So the checklist is the source of truth and the response is evidence:

* every section in the checklist appears, in the checklist's order
* every item in each section appears, whether or not the model returned it
* an item the model did not return becomes ``not_assessed`` — explicitly not
  the same as "the agent did not say it"
* counts are recomputed here, never taken from the response
* the final determination is derived from the reconciled items, so a verdict
  can never disagree with the rows beneath it
* auto-fail phrases are filtered to the ones the org actually configured, so a
  hallucinated violation cannot fail a call

Nothing here asks the customer to verify anything. A checklist that can be
edited is a checklist that must grade correctly by construction.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

COVERED = "covered"
NOT_COVERED = "not_covered"
NOT_ASSESSED = "not_assessed"

# Machine-readable verdicts, stored on Report.verdict. The prose determination
# is for people; these are for queries.
PASS = "pass"
FAIL = "fail"
CRITICAL = "critical"


def _norm(text: str) -> str:
    """Loose key for matching a returned name back to a checklist name."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _section_key(section: dict) -> str:
    key = section.get("key")
    if key:
        return key
    return re.sub(r"[^a-z0-9]+", "_", (section.get("name") or "section").lower()).strip("_") or "section"


def _index_returned_sections(raw: dict) -> tuple[dict, dict]:
    by_key, by_name = {}, {}
    for s in raw.get("sections") or []:
        if not isinstance(s, dict):
            continue
        if s.get("key"):
            by_key.setdefault(str(s["key"]), s)
        if s.get("name"):
            by_name.setdefault(_norm(str(s["name"])), s)
    return by_key, by_name


def _index_returned_items(section: dict) -> tuple[dict, list]:
    by_name, leftovers = {}, []
    for i in section.get("items") or []:
        if isinstance(i, dict) and i.get("name"):
            by_name.setdefault(_norm(str(i["name"])), i)
        elif isinstance(i, dict):
            leftovers.append(i)
    return by_name, leftovers


def _clean_status(value) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in (COVERED, "yes", "true", "pass", "met"):
        return COVERED
    if v in (NOT_COVERED, "missing", "no", "false", "fail", "not_met"):
        return NOT_COVERED
    return NOT_ASSESSED


def normalize_report(raw: dict | None, checklist: dict | None) -> dict:
    """Return a report guaranteed to describe exactly `checklist`."""
    raw = raw if isinstance(raw, dict) else {}
    checklist = checklist if isinstance(checklist, dict) else {}
    ret_by_key, ret_by_name = _index_returned_sections(raw)

    sections_out: list[dict] = []
    missing_from_response = 0
    failed_sections: list[str] = []

    for spec in checklist.get("sections") or []:
        if not isinstance(spec, dict):
            continue
        key = _section_key(spec)
        name = spec.get("name") or key
        returned = ret_by_key.get(key) or ret_by_name.get(_norm(name)) or {}
        ret_items, _ = _index_returned_items(returned)

        items_out = []
        covered = required_total = 0
        section_has_miss = False

        for item_spec in spec.get("items") or []:
            if not isinstance(item_spec, dict) or not item_spec.get("name"):
                continue
            item_name = str(item_spec["name"])
            is_required = bool(item_spec.get("required", True))
            got = ret_items.get(_norm(item_name))

            if got is None:
                missing_from_response += 1
                status = NOT_ASSESSED
                timestamp, evidence = None, ""
            else:
                status = _clean_status(got.get("status"))
                timestamp = got.get("timestamp")
                evidence = got.get("evidence") or ""

            if timestamp in ("", "null", "None", "—"):
                timestamp = None

            items_out.append({
                "name": item_name,
                "required": is_required,
                "status": status,
                "timestamp": timestamp,
                "evidence": evidence,
            })

            if is_required:
                required_total += 1
                if status == COVERED:
                    covered += 1
                else:
                    section_has_miss = True

        if section_has_miss:
            failed_sections.append(name)

        sections_out.append({
            "key": key,
            "name": name,
            "covered_count": covered,
            "total_count": required_total,
            "items": items_out,
        })

    # Auto-fail phrases: only ones this org configured. A phrase the model
    # invented must never be able to fail a call.
    configured = {
        _norm(str(p.get("phrase", "")))
        for p in (checklist.get("auto_fail_phrases") or [])
        if isinstance(p, dict) and p.get("phrase")
    }
    raw_af = raw.get("auto_fail_phrases")
    raw_af = raw_af if isinstance(raw_af, dict) else {}
    kept, discarded = [], 0
    for hit in raw_af.get("phrases") or []:
        if not isinstance(hit, dict):
            continue
        if configured and _norm(str(hit.get("phrase", ""))) not in configured:
            discarded += 1
            continue
        kept.append({
            "phrase": hit.get("phrase", ""),
            "timestamp": hit.get("timestamp") or None,
            "speaker": hit.get("speaker") or "",
            "quote": hit.get("quote") or "",
            "violation": hit.get("violation") or "",
        })

    # Determination is derived, never trusted, so a verdict can never disagree
    # with the rows underneath it. `verdict` is the same decision in a form a
    # query can index; the prose is for the reader.
    if kept:
        determination, verdict = "CRITICAL FAIL", CRITICAL
    elif not sections_out:
        determination = raw.get("final_determination") or "FAIL — no checklist sections"
        verdict = FAIL
    elif not failed_sections:
        determination, verdict = "PASS", PASS
    elif len(failed_sections) == 1:
        determination, verdict = f"FAIL — {failed_sections[0]}", FAIL
    else:
        determination, verdict = "FAIL — Multiple sections", FAIL

    unassessed = sum(1 for s in sections_out for i in s["items"] if i["status"] == NOT_ASSESSED)
    if missing_from_response or discarded:
        logger.warning(
            "Report reconciliation: %d checklist item(s) absent from the model "
            "response, %d unconfigured auto-fail phrase(s) discarded",
            missing_from_response, discarded,
        )

    return {
        "final_determination": determination,
        "verdict": verdict,
        "summary": str(raw.get("summary") or ""),
        "sections": sections_out,
        "auto_fail_phrases": {"detected": bool(kept), "phrases": kept},
        "_reconciliation": {
            "items_not_assessed": unassessed,
            "phrases_discarded": discarded,
        },
    }
