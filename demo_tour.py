"""The public product walkthrough at /tour.

Built over the real report page rather than screenshots, deliberately. Screens
captured today are wrong the moment the UI changes — and this report page
changed three times in the week this was written. A tour driven by the live
template can never disagree with the product, because it *is* the product.

Everything here is synthetic: an invented agent, an invented client, an
invented creditor, and an audio file generated from noise. No customer data
reaches this page, which matters because it is served to anonymous visitors.

The narration is a QA manager called Dana walking through a Monday morning.
That framing is doing real work: a numbered list of features is exhausting by
step four, whereas somebody's job is a story you follow to the end.
"""
from __future__ import annotations

import math
import os
import struct
import wave
from datetime import date
from types import SimpleNamespace

# ── The call ────────────────────────────────────────────────────────────────
# 6:19, which is a realistic approval call. Long enough that the timeline and
# the skip controls have something to do, short enough to sit through.
DEMO_DURATION = 379
DEMO_AUDIO_REL = "demo/walkthrough-call.wav"

AGENT_NAME = "Marcus Webb"
CLIENT_NAME = "Ray"

# Timestamps are the spine of the whole thing: every answer in the report points
# at one, the transcript lines carry the same ones, and the tour steps reference
# them. Defined once here so they cannot drift apart.
TS = {
    "credit": ("0:32", 32),
    "fee": ("1:05", 65),
    "cancel": (None, None),          # never said — the point of the demo
    "guarantee": ("2:41", 161),
    "consolidation": ("4:12", 252),
}


def _fmt(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


TRANSCRIPT_LINES = [
    (0, 6, "A", f"Thanks for holding, {CLIENT_NAME}. I've got your file up now."),
    (7, 18, "B", "Appreciate it. I just want to know what this actually does to me."),
    (19, 31, "A", "Of course. Let me take you through it properly before you decide anything."),
    (32, 44, "A", "Enrolling in this program may adversely affect your credit rating, "
                  "and results vary by creditor."),
    (45, 58, "B", "That's what I figured. How much is it going to cost me?"),
    (65, 79, "A", "Our fee is twenty-two percent of the total enrolled debt, and it's "
                  "built into the monthly payment — nothing separate."),
    (80, 96, "B", "Okay. And how long are we talking?"),
    (97, 118, "A", "Most people are looking at thirty-six to forty-eight months, "
                   "depending on how quickly the account builds."),
    (119, 140, "B", "My brother did something like this and it took longer than they said."),
    (141, 160, "A", "That happens. The timeline moves with what the creditors agree to."),
    (161, 176, "A", "I do want to be clear — none of this is guaranteed. "
                    "No one can promise a creditor will settle."),
    (177, 199, "B", "Right. So what happens if I stop paying them directly?"),
    (200, 228, "A", "Your accounts will go delinquent while we build the settlement fund. "
                    "That's the part that shows up on your credit."),
    (229, 251, "B", "And I've already got one of those consolidation things from before."),
    (252, 268, "B", "It was a consolidation loan through another company. Never went anywhere."),
    (269, 292, "A", "Understood. We'd only enroll the accounts that aren't tied up in that."),
    (293, 318, "B", "Alright. What do you need from me?"),
    (319, 344, "A", "I'll send the agreement over and walk you through the questions."),
    (345, 379, "A", "You'll get an email in the next few minutes. Take your time with it."),
]


def _report_json() -> dict:
    """The graded report, in exactly the shape report_normalizer produces."""
    return {
        "final_determination": "FAIL — Approval Script",
        "verdict": "fail",
        "summary": (
            f"{AGENT_NAME} covered the credit impact and the fee, and disclaimed a "
            "guarantee without being prompted. The cancellation policy was never "
            "stated. The client referred to a prior consolidation loan through "
            "another company, which is flagged below for review."
        ),
        "sections": [
            {
                "name": "Approval Script", "key": "approval_script",
                "covered_count": 3, "total_count": 4,
                "items": [
                    {"name": "Disclose that enrollment may affect credit rating",
                     "status": "covered", "required": True,
                     "timestamp": TS["credit"][0],
                     "evidence": "Enrolling in this program may adversely affect your "
                                 "credit rating, and results vary by creditor."},
                    {"name": "State the fee as a percentage of enrolled debt",
                     "status": "covered", "required": True,
                     "timestamp": TS["fee"][0],
                     "evidence": "Our fee is twenty-two percent of the total enrolled debt."},
                    {"name": "State the cancellation policy",
                     "status": "missed", "required": True,
                     "timestamp": None,
                     "evidence": "Not mentioned at any point in the call."},
                    {"name": "Set expectations on program length",
                     "status": "covered", "required": True,
                     "timestamp": "1:37",
                     "evidence": "Most people are looking at thirty-six to forty-eight months."},
                ],
            },
            {
                "name": "Post-Enrollment Script", "key": "post_enrollment",
                "covered_count": 2, "total_count": 3,
                "items": [
                    {"name": "Explain what happens to existing accounts",
                     "status": "covered", "required": True, "timestamp": "3:20",
                     "evidence": "Your accounts will go delinquent while we build the "
                                 "settlement fund."},
                    {"name": "Confirm the client understands the risk of legal action",
                     "status": "not_assessed", "required": True, "timestamp": None,
                     "evidence": "The grader returned no verdict for this requirement."},
                    {"name": "Confirm the monthly payment amount",
                     "status": "covered", "required": True, "timestamp": "1:05",
                     "evidence": "It's built into the monthly payment — nothing separate."},
                ],
            },
        ],
        "auto_fail_phrases": {
            "detected": False,
            "agent_count": 0,
            "client_count": 1,
            "disclaimed_count": 1,
            "phrases": [
                {"phrase": "guaranteed", "timestamp": TS["guarantee"][0],
                 "speaker": "Speaker A (Agent)", "spoken_by": "agent",
                 "is_violation": False,
                 "quote": "I do want to be clear — none of this is guaranteed.",
                 "violation": "Not a violation — the agent is disclaiming a guarantee."},
                {"phrase": "consolidation loan", "timestamp": TS["consolidation"][0],
                 "speaker": "Speaker B (Client)", "spoken_by": "client",
                 "is_violation": False,
                 "quote": "It was a consolidation loan through another company.",
                 "violation": "Spoken by the client about a prior arrangement."},
            ],
        },
        "program_flip": {
            "detected": True,
            "reason": "The client describes a prior consolidation loan through another "
                      "company. Whether those accounts are being enrolled here is worth "
                      "confirming.",
            "evidence": [
                {"timestamp": TS["consolidation"][0], "speaker": "Speaker B (Client)",
                 "quote": "It was a consolidation loan through another company. "
                          "Never went anywhere."},
            ],
        },
        "ineligible_accounts": [],
        "_reconciliation": {"items_not_assessed": 1, "phrases_discarded": 0},
    }


def _transcript_json() -> dict:
    return {
        "utterances": [
            {"speaker": sp, "start": a * 1000, "end": b * 1000, "text": t}
            for a, b, sp, t in TRANSCRIPT_LINES
        ],
        "text": " ".join(t for _, _, _, t in TRANSCRIPT_LINES),
    }


def demo_call(audio_url: str | None):
    """A Call-shaped object the real report template and view model can consume.

    SimpleNamespace rather than a detached SQLAlchemy row: nothing here should
    be capable of reaching the database, and a plain object makes that obvious
    at a glance instead of depending on session state.
    """
    report = SimpleNamespace(
        report_json=_report_json(),
        overrides_json={},
        verdict="fail",
        pass_fail_status="FAIL — Approval Script",
        reviewed_at=None,
        reviewed_by_user_id=None,
        review_outcome=None,
        review_note=None,
    )
    return SimpleNamespace(
        id="walkthrough",
        internal_id="DEMO-4417",
        filename="20260810-0914_5550142.mp3",
        status="complete",
        duration=DEMO_DURATION,
        call_date=date(2026, 8, 10),
        client_phone="(555) 014-2200",
        audio_file_url=audio_url,
        agent=SimpleNamespace(id="demo-agent", name=AGENT_NAME),
        report=report,
        transcript=SimpleNamespace(raw_transcript_json=_transcript_json()),
    )


# ── The narration ───────────────────────────────────────────────────────────
#
# Dana is invented, and labelled as such on the page. She is a composite of the
# job, not a testimonial — the site must never imply a customer said something
# a customer did not say.
#
# `target` is a CSS selector on the real page. A step whose target is missing is
# skipped at runtime rather than pointing at nothing, so the tour degrades
# instead of breaking when the report page changes.
STEPS = [
    {"target": None, "title": "Monday, 9:14am",
     "body": "I'm Dana. I review calls for a debt settlement firm — the kind that "
             "gets audited. Forty calls landed over the weekend. I used to listen "
             "to four of them and hope I picked the right four."},
    {"target": ".rp-verdict", "title": "The answer first",
     "body": "Every call arrives already graded against our own checklist. This one "
             "failed, and I can see that before I've listened to a second of it."},
    {"target": ".rp-transport", "title": "The whole call on one line",
     "body": "Speaker A is the agent, Speaker B is the client. Every diamond is a "
             "moment the grader found something. I can skip ten seconds at a time, "
             "or run it at 2× — these calls are rarely short."},
    {"target": "[data-tour='covered']", "title": "What was said, and when",
     "body": "The credit disclosure was made at 0:32. It's not a checkmark — it's the "
             "sentence he actually said, with the second it happened. I click the "
             "timestamp and hear it."},
    {"target": "[data-tour='missed']", "title": "What wasn't",
     "body": "The cancellation policy never came up. No timestamp, because nothing "
             "happened to point at. This is the line I'd be asked about in an audit."},
    {"target": "[data-tour='not-assessed']", "title": "And what it wasn't sure about",
     "body": "This one came back without a verdict. It's shown separately rather than "
             "counted as a miss — the agent shouldn't lose points because the grader "
             "was unsure."},
    {"target": ".rp-panel--flip, [role='note']", "title": "Context before judgment",
     "body": "The client mentioned a consolidation loan through another company. That "
             "isn't a violation, but it changes which disclosures matter — so it's "
             "flagged for me rather than decided for me."},
    {"target": "[data-tour='phrases']", "title": "Prohibited language, in context",
     "body": "Marcus said the word “guaranteed” — while disclaiming a guarantee. "
             "The client said “consolidation loan” about his own old account. "
             "Neither counts against him, and both are shown so I can check."},
    {"target": "[data-tour='override']", "title": "When the grader is wrong",
     "body": "It does get things wrong. I approve the item and the score recalculates — "
             "on this report, and on Marcus's scorecard."},
    {"target": "#signoff", "title": "Then I'm done with it",
     "body": "I record what happens to the call and it leaves my queue. Signing off "
             "doesn't change anyone's score — that's what the overrides are for. "
             "Everyone assumes the opposite, so it says so right there."},
    {"target": None, "title": "Forty calls, not four",
     "body": "That took me about ninety seconds. The other thirty-nine are waiting, "
             "already graded, sorted worst first.",
     "cta": True},
]


def ensure_audio(static_folder: str) -> str | None:
    """Generate the walkthrough's audio once, and return its static-relative path.

    Synthesised rather than recorded, for the obvious reason: this page is
    public and no real client's voice belongs on it. Telephone-band mono at
    8kHz, which is both what a call recording actually sounds like and small
    enough to serve — roughly 3MB for six minutes.

    Deliberately not speech. The transcript carries the words; this exists so
    the player, the scrubber and the timestamps are genuinely operable rather
    than a dead control.
    """
    path = os.path.join(static_folder, DEMO_AUDIO_REL)
    if os.path.exists(path):
        return DEMO_AUDIO_REL
    os.makedirs(os.path.dirname(path), exist_ok=True)

    rate = 8000
    frames = bytearray()
    # A quiet two-tone hum that alternates with the speaker turns, so the
    # waveform under the playhead corresponds to something on screen.
    for i in range(rate * DEMO_DURATION):
        t = i / rate
        speaker_a = any(a <= t <= b and sp == "A" for a, b, sp, _ in TRANSCRIPT_LINES)
        talking = any(a <= t <= b for a, b, _, _ in TRANSCRIPT_LINES)
        if not talking:
            frames.append(128)
            continue
        f = 220.0 if speaker_a else 165.0
        amp = 10 * (0.6 + 0.4 * math.sin(2 * math.pi * 3.1 * t))
        frames.append(int(128 + amp * math.sin(2 * math.pi * f * t)))

    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(1)
        fh.setframerate(rate)
        fh.writeframes(bytes(frames))
    return DEMO_AUDIO_REL
