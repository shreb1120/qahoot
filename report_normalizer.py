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
# Model dropped required items — a grading gap, not an agent miss. Must never
# be folded into FAIL or a coaching miss rate.
INCOMPLETE = "incomplete"


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
    if v in (NOT_COVERED, "missing", "missed", "no", "false", "fail", "not_met"):
        return NOT_COVERED
    return NOT_ASSESSED


def _spoken_by(hit: dict) -> str:
    """Who said it: "agent", "client", or "" when genuinely unknown.

    The grader is asked for an explicit `spoken_by`, which is the reliable
    answer. The fallback reads the free-text `speaker` label, because reports
    graded before that field existed carry attribution only there — in practice
    as strings like "Speaker B (Client - Isaac)".

    Unknown resolves to agent-attributable, deliberately. Silently excusing a
    phrase nobody could attribute would hide real misconduct, which is a worse
    failure than the one being fixed here; a reviewer can see the attribution on
    the report and override.
    """
    explicit = str(hit.get("spoken_by") or "").strip().lower()
    if explicit in ("agent", "client"):
        return explicit

    label = str(hit.get("speaker") or "").lower()
    if "client" in label or "customer" in label:
        return "client"
    if "agent" in label:
        return "agent"
    return ""


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
        # Only a genuine not_covered miss fails the section. not_assessed is a
        # grader gap — treating it as FAIL made model dropouts look like the
        # agent skipped disclosures (see tests/test_verdict.py history).
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
                elif status == NOT_COVERED:
                    section_has_miss = True
                # NOT_ASSESSED: counted via unassessed_required below; not a miss.

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
            "spoken_by": _spoken_by(hit),
            # Absent means true: an unmarked phrase must keep failing the call,
            # so a grader that omits the field cannot quietly excuse anything.
            "is_violation": hit.get("is_violation") is not False,
            "quote": hit.get("quote") or "",
            "violation": hit.get("violation") or "",
        })

    # Only the agent can fail the call.
    #
    # This tool grades the agent. A client using a prohibited phrase to describe
    # their own situation is not misconduct by the person under review — but for
    # a long time it was scored as one: any kept phrase became CRITICAL FAIL
    # regardless of who spoke it. Call 888454 covered all 33 required items and
    # was marked CRITICAL FAIL because the client said "consolidation loan"
    # about his own prior account. The grader had correctly recorded
    # `speaker: "Speaker B (Client - Isaac)"` and written "spoken by the CLIENT,
    # not the agent" into the violation text. The verdict logic simply never
    # looked.
    #
    # Client-spoken phrases stay in the report: a reviewer may well want to know
    # the framing was used and the agent let it stand. They just cannot fail
    # anyone.
    # Two conditions, and the second was learned the hard way. Compliance
    # scripts are full of mandatory disclaimers that contain the prohibited
    # words in order to deny them — "this program is not a 0% payment plan",
    # "I do want to inform you, it's not guaranteed", "we are not making your
    # monthly payments". Call 888454 was failed three times over for reading
    # those correctly.
    #
    # The grader had already worked this out on its own and written "Not a
    # violation — the agent is reading a required negating disclosure" into the
    # violation text. It had nowhere structured to say so, so the verdict could
    # not act on it. `is_violation` is that place.
    agent_hits = [h for h in kept
                  if h["spoken_by"] != "client" and h["is_violation"]]

    unassessed = sum(1 for s in sections_out for i in s["items"] if i["status"] == NOT_ASSESSED)
    unassessed_required = sum(
        1 for s in sections_out for i in s["items"]
        if i["status"] == NOT_ASSESSED and i.get("required", True)
    )

    # Determination is derived, never trusted, so a verdict can never disagree
    # with the rows underneath it. `verdict` is the same decision in a form a
    # query can index; the prose is for the reader.
    #
    # Order: critical agent hits win; real misses FAIL; grading gaps alone are
    # INCOMPLETE (re-run), never FAIL; only then PASS.
    if agent_hits:
        determination, verdict = "CRITICAL FAIL", CRITICAL
    elif not sections_out:
        determination = raw.get("final_determination") or "FAIL — no checklist sections"
        verdict = FAIL
    elif failed_sections:
        if len(failed_sections) == 1:
            determination, verdict = f"FAIL — {failed_sections[0]}", FAIL
        else:
            determination, verdict = "FAIL — Multiple sections", FAIL
    elif unassessed_required:
        determination, verdict = "INCOMPLETE — grading gap", INCOMPLETE
    else:
        determination, verdict = "PASS", PASS
    if missing_from_response or discarded:
        logger.warning(
            "Report reconciliation: %d checklist item(s) absent from the model "
            "response, %d unconfigured auto-fail phrase(s) discarded",
            missing_from_response, discarded,
        )

    # Program flip: context for the reviewer, never an input to the verdict.
    #
    # Deliberately normalised *after* `determination` and `verdict` are already
    # decided above, so there is no ordering in which this block could change
    # them. A client who is already in another program is not committing a
    # violation — but which disclosures are meaningful may differ (credit impact
    # on accounts already defaulted under a prior program, for one), and that is
    # a judgment for the person reviewing, not for the grader.
    raw_flip = raw.get("program_flip")
    raw_flip = raw_flip if isinstance(raw_flip, dict) else {}
    flip_evidence = [
        {
            "timestamp": e.get("timestamp") or None,
            "speaker": e.get("speaker") or "",
            "quote": e.get("quote") or "",
        }
        for e in (raw_flip.get("evidence") or [])
        if isinstance(e, dict) and e.get("quote")
    ]
    # A bare `detected: true` with nothing to show would put an unfalsifiable
    # banner on the report. Evidence or a reason, or it does not appear.
    flip_reason = str(raw_flip.get("reason") or "").strip()
    flip_detected = bool(raw_flip.get("detected")) and bool(flip_evidence or flip_reason)

    # Accounts the client said something disqualifying about. Same contract as
    # the program flip: normalised after the verdict is already decided, so it
    # cannot participate in it, and dropped unless it carries a quote a reviewer
    # can check.
    #
    # The reason_code set is closed. An open one lets the model invent
    # categories, and a category nobody defined is a category nobody has decided
    # what to do about.
    INELIGIBLE_REASONS = {"prior_settlement", "secured_vehicle", "litigation"}
    ineligible = []
    for raw_item in raw.get("ineligible_accounts") or []:
        if not isinstance(raw_item, dict):
            continue
        code = str(raw_item.get("reason_code") or "").strip().lower()
        quote = str(raw_item.get("quote") or "").strip()
        if code not in INELIGIBLE_REASONS or not quote:
            continue
        ineligible.append({
            "reason_code": code,
            "account": str(raw_item.get("account") or "").strip(),
            "timestamp": raw_item.get("timestamp") or None,
            "speaker": str(raw_item.get("speaker") or ""),
            "quote": quote,
            "note": str(raw_item.get("note") or "").strip(),
        })

    return {
        "final_determination": determination,
        "verdict": verdict,
        "summary": str(raw.get("summary") or ""),
        "sections": sections_out,
        "auto_fail_phrases": {
            "detected": bool(agent_hits),
            "phrases": kept,
            # Three distinct populations, counted separately because the report
            # says something different about each. Deriving "client" as
            # everything-that-is-not-a-violation lumped agent-spoken disclaimers
            # in with it and printed a plainly false sentence.
            "agent_count": len(agent_hits),
            "client_count": sum(1 for h in kept if h["spoken_by"] == "client"),
            "disclaimed_count": sum(1 for h in kept
                                    if h["spoken_by"] != "client"
                                    and not h["is_violation"]),
        },
        "program_flip": {
            "detected": flip_detected,
            "reason": flip_reason,
            "evidence": flip_evidence,
        },
        "ineligible_accounts": ineligible,
        "_reconciliation": {
            "items_not_assessed": unassessed,
            "phrases_discarded": discarded,
        },
    }
