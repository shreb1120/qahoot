"""The "possible program flip" note.

A client already enrolled in another debt-relief programme changes which
disclosures are meaningful — credit impact on accounts already defaulted under
a prior programme, for one. That is a judgment for the reviewer, so the grader
flags it and says nothing about whether it excuses anything.

The load-bearing property is that it cannot touch the verdict. A note that can
silently move a pass to a fail, or the reverse, is worse than no note.
"""
import pytest

from report_normalizer import normalize_report


CHECKLIST = {
    "sections": [{"name": "Approval", "key": "approval",
                  "items": [{"name": "Disclose fees", "required": True}]}],
    "auto_fail_phrases": [{"phrase": "guaranteed"}],
}


def _raw(flip=None, covered=True, phrases=None):
    return {
        "sections": [{"name": "Approval", "items": [
            {"name": "Disclose fees",
             "status": "covered" if covered else "missed",
             "evidence": "", "timestamp": "01:00"}]}],
        "auto_fail_phrases": {"detected": bool(phrases), "phrases": phrases or []},
        "program_flip": flip,
        "final_determination": "PASS",
        "summary": "s",
    }


# ─────────────────── it must never move the verdict ───────────────────

@pytest.mark.parametrize("covered,expected", [(True, "pass"), (False, "fail")])
def test_a_flip_does_not_change_the_verdict(covered, expected):
    flip = {"detected": True, "reason": "Client mentions Freedom Debt",
            "evidence": [{"timestamp": "37:42", "speaker": "B", "quote": "a Freedom Debt"}]}
    with_flip = normalize_report(_raw(flip=flip, covered=covered), CHECKLIST)
    without = normalize_report(_raw(flip=None, covered=covered), CHECKLIST)

    assert with_flip["verdict"] == expected
    assert with_flip["verdict"] == without["verdict"]
    assert with_flip["final_determination"] == without["final_determination"]


def test_a_flip_cannot_rescue_a_critical_fail():
    """The dangerous direction. A flip is context, not an excuse."""
    flip = {"detected": True, "reason": "Already enrolled elsewhere", "evidence": []}
    out = normalize_report(
        _raw(flip=flip, phrases=[{"phrase": "guaranteed", "timestamp": "10:00",
                                  "speaker": "A", "quote": "guaranteed"}]),
        CHECKLIST)
    assert out["verdict"] == "critical"
    assert out["program_flip"]["detected"] is True


# ─────────────────── what gets shown ───────────────────

def test_the_reason_and_evidence_survive():
    flip = {"detected": True, "reason": "Client names Freedom Debt at 37:42",
            "evidence": [{"timestamp": "37:42", "speaker": "Speaker B (Client)",
                          "quote": "one that I want to pay off is a Freedom Debt"}]}
    out = normalize_report(_raw(flip=flip), CHECKLIST)["program_flip"]

    assert out["detected"] is True
    assert "Freedom Debt" in out["reason"]
    assert out["evidence"][0]["timestamp"] == "37:42"
    assert "Freedom Debt" in out["evidence"][0]["quote"]


def test_a_bare_detected_true_with_nothing_to_show_is_dropped():
    """An unfalsifiable banner is worse than none — a reviewer cannot check it,
    so it becomes noise they learn to scroll past."""
    out = normalize_report(_raw(flip={"detected": True}), CHECKLIST)["program_flip"]
    assert out["detected"] is False


def test_evidence_without_a_quote_is_dropped():
    flip = {"detected": True, "reason": "", "evidence": [{"timestamp": "01:00"}]}
    out = normalize_report(_raw(flip=flip), CHECKLIST)["program_flip"]
    assert out["evidence"] == []
    assert out["detected"] is False


def test_no_flip_block_at_all_is_fine():
    """Reports graded before this existed must still normalise."""
    raw = _raw()
    del raw["program_flip"]
    out = normalize_report(raw, CHECKLIST)
    assert out["program_flip"]["detected"] is False
    assert out["verdict"] == "pass"


@pytest.mark.parametrize("junk", [None, "yes", [], 0, {"detected": "maybe"}])
def test_a_malformed_flip_block_never_breaks_a_report(junk):
    out = normalize_report(_raw(flip=junk), CHECKLIST)
    assert out["verdict"] == "pass"
    assert isinstance(out["program_flip"]["detected"], bool)


# ═══════════════ accounts the client said something disqualifying about ═══════════════
#
# Same contract as the flip note: context for the reviewer, never an input to
# the verdict. Whether the account was actually enrolled is what the reviewer
# goes and checks — the report must not pre-judge that.

def _acct(**kw):
    base = {"reason_code": "litigation", "account": "Chase card",
            "timestamp": "23:52", "speaker": "Speaker B (Client)",
            "quote": "yeah they already took me to court on that one",
            "note": "Client confirms suit"}
    base.update(kw)
    return base


def _raw_ineligible(accounts, covered=True):
    r = _raw(covered=covered)
    r["ineligible_accounts"] = accounts
    return r


@pytest.mark.parametrize("covered,expected", [(True, "pass"), (False, "fail")])
def test_ineligible_accounts_do_not_change_the_verdict(covered, expected):
    out = normalize_report(_raw_ineligible([_acct()], covered=covered), CHECKLIST)
    assert out["verdict"] == expected
    assert len(out["ineligible_accounts"]) == 1


@pytest.mark.parametrize("code", ["prior_settlement", "secured_vehicle", "litigation"])
def test_each_reason_code_is_kept(code):
    out = normalize_report(_raw_ineligible([_acct(reason_code=code)]), CHECKLIST)
    assert out["ineligible_accounts"][0]["reason_code"] == code


def test_an_invented_reason_code_is_dropped():
    """The set is closed on purpose — a category nobody defined is a category
    nobody has decided what to do about."""
    out = normalize_report(_raw_ineligible([_acct(reason_code="vibes")]), CHECKLIST)
    assert out["ineligible_accounts"] == []


def test_an_entry_without_a_quote_is_dropped():
    """A reviewer has to be able to check it against the recording."""
    out = normalize_report(_raw_ineligible([_acct(quote="")]), CHECKLIST)
    assert out["ineligible_accounts"] == []


def test_the_common_case_is_an_empty_list():
    out = normalize_report(_raw_ineligible([]), CHECKLIST)
    assert out["ineligible_accounts"] == []
    assert out["verdict"] == "pass"


def test_reports_graded_before_this_existed_still_normalise():
    raw = _raw()
    assert "ineligible_accounts" not in raw
    out = normalize_report(raw, CHECKLIST)
    assert out["ineligible_accounts"] == []


@pytest.mark.parametrize("junk", [None, "yes", 0, [None, "x", 5]])
def test_a_malformed_list_never_breaks_a_report(junk):
    r = _raw()
    r["ineligible_accounts"] = junk
    out = normalize_report(r, CHECKLIST)
    assert out["verdict"] == "pass"
    assert isinstance(out["ineligible_accounts"], list)


def test_the_prompt_teaches_the_two_distinctions_that_cause_false_alarms():
    """The agent *asking* the screening question is the script working, and a
    car payment in a budget review is not an enrolled debt. Both were true of
    call 888454, which would otherwise have produced two confident false
    alarms on a call that was clean."""
    from prompt_builder import build_system_prompt
    p = build_system_prompt({
        "sections": [{"name": "S", "items": [{"name": "i"}]}],
        "auto_fail_phrases": [],
        "grader_extensions": ["program_flip", "ineligible_accounts"],
    }).lower()
    assert "asking is not a finding" in p
    assert "expense" in p and "enrolled" in p


def test_horizontal_prompts_omit_debt_relief_screening_language():
    from prompt_builder import build_system_prompt
    p = build_system_prompt({
        "sections": [{"name": "S", "items": [{"name": "i"}]}],
        "auto_fail_phrases": [],
    }).lower()
    assert "asking is not a finding" not in p
    assert "program flip" not in p
    assert "prior_settlement" not in p


# ═══════════════ the words alone are not the violation ═══════════════

def test_the_prompt_distinguishes_asserting_from_disclaiming():
    """Call 888454 was marked CRITICAL FAIL twice over for phrases the agent
    used to *deny* the very claims they name:

        "I do want to inform you, it's not guaranteed"
        "this program ... is not a 0% payment plan"

    Both are required disclaimers. Compliance scripts in this industry are full
    of them, often word for word — which is precisely why the literal prohibited
    words appear in a well-run call. Matching on the words alone inverts the
    meaning of the call and fails the agents who are doing it right."""
    from prompt_builder import build_system_prompt
    p = build_system_prompt({"sections": [{"name": "S", "items": [{"name": "i"}]}],
                             "auto_fail_phrases": [{"phrase": "guaranteed"}]}).lower()
    assert "asserts" in p
    assert "not a 0% payment plan" in p
    assert "it's not guaranteed" in p
    assert "is_violation = false" in p, \
        "the grader needs a structured way to say a disclaimer is not a violation"


# ═══════════════ a disclaimer is not a violation ═══════════════

def _phrase(**kw):
    base = {"phrase": "guaranteed", "timestamp": "35:25", "speaker": "A",
            "spoken_by": "agent", "quote": "it's not guaranteed",
            "violation": "Not a violation — explicit disclaimer",
            "is_violation": False}
    base.update(kw)
    return base


def _with_phrases(phrases):
    r = _raw()
    r["auto_fail_phrases"] = {"detected": True, "phrases": phrases}
    return r


def test_a_disclaimed_phrase_does_not_fail_the_call():
    """Call 888454 was failed three times over for reading required disclaimers:
    "this program is not a 0% payment plan", "it's not guaranteed", "we are not
    making your monthly payments". Every one is the agent doing their job."""
    out = normalize_report(_with_phrases([_phrase()]), CHECKLIST)
    assert out["verdict"] == "pass"
    assert out["auto_fail_phrases"]["agent_count"] == 0


def test_the_disclaimer_still_appears_on_the_report():
    """A reviewer may well want to confirm the disclosure was read."""
    out = normalize_report(_with_phrases([_phrase()]), CHECKLIST)
    assert len(out["auto_fail_phrases"]["phrases"]) == 1
    assert out["auto_fail_phrases"]["phrases"][0]["is_violation"] is False


def test_an_asserted_phrase_still_fails_the_call():
    """The whole point of the tool. Do not fix false positives by going blind."""
    out = normalize_report(
        _with_phrases([_phrase(quote="this is guaranteed to work", is_violation=True)]),
        CHECKLIST)
    assert out["verdict"] == "critical"


def test_a_missing_is_violation_still_fails():
    """Absent means true. A grader that omits the field must not quietly excuse
    a real violation."""
    p = _phrase()
    del p["is_violation"]
    out = normalize_report(_with_phrases([p]), CHECKLIST)
    assert out["verdict"] == "critical"


def test_a_real_violation_among_disclaimers_still_fails():
    out = normalize_report(_with_phrases([
        _phrase(quote="not a 0% payment plan", is_violation=False),
        _phrase(quote="your rate will be 0%", is_violation=True),
    ]), CHECKLIST)
    assert out["verdict"] == "critical"
    assert out["auto_fail_phrases"]["agent_count"] == 1
