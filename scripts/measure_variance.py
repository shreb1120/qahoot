#!/usr/bin/env python3
"""Grade the same call several times and measure how much the answer moves.

Why this exists
---------------
A compliance report may end up in front of an auditor. "The tool said so" is
only a defence if the tool says the same thing twice. This measures whether it
does, so the answer is a number rather than an impression.

It grades from the **stored transcript** and never re-transcribes: the audio has
not changed, transcription is separately billed, and holding the transcript
fixed is what isolates grading variance from transcription variance. (Measured
on real duplicate uploads, transcripts of the same audio come back 99.8-100%
identical, so grading is where the movement is.)

It never writes to the database.

Usage
-----
    python3 scripts/measure_variance.py 888454 --runs 5
    python3 scripts/measure_variance.py 888454 1889 --runs 3 --out /tmp/v.json
    python3 scripts/measure_variance.py --all --runs 3 --max-duration 1500

Reading the output
------------------
`verdict agreement` is the headline: how often the most common verdict won.
Anything below 100% means two people running the same call on the same day can
get different answers.

`item agreement` is the mean, across requirements, of how often the most common
status won. It is the more useful number for improving the prompt, because it
names *which* requirements are unstable — those are the ones whose wording the
grader is reading inconsistently.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import dotenv_values

_ENV = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), ".env"))
os.environ.update({k: v for k, v in _ENV.items() if v})

import anthropic  # noqa: E402
import psycopg2  # noqa: E402

import pipeline  # noqa: E402
from prompt_builder import build_system_prompt, build_user_message  # noqa: E402
from report_normalizer import normalize_report  # noqa: E402

# Anthropic list price for the grading model, dollars per million tokens.
_IN_PER_M, _OUT_PER_M = 5.0, 25.0


def _connect():
    return psycopg2.connect(_ENV["DATABASE_URL"])


def _load(cur, internal_id):
    cur.execute(
        """select c.id, c.duration, o.name, p.script_sections_json,
                  t.raw_transcript_json, r.verdict
           from calls c
           join transcripts t on t.call_id = c.id
           join compliance_profiles p on p.id = c.compliance_profile_id
           join organizations o on o.id = c.org_id
           left join reports r on r.call_id = c.id
           where c.internal_id = %s and c.status = 'complete'
           order by c.upload_date desc limit 1""",
        (internal_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"No completed call with transcript for id {internal_id!r}")
    call_id, duration, org, checklist, transcript, stored = row
    return {
        "call_id": call_id, "duration": duration, "org": org,
        "checklist": checklist, "stored_verdict": stored,
        "text": _flatten(transcript),
    }


def _flatten(transcript) -> str:
    lines = []
    for u in (transcript or {}).get("utterances") or []:
        ts = (u.get("start") or 0) // 1000
        lines.append(f"[{ts // 60:02d}:{ts % 60:02d}] {u.get('speaker', '?')}: {u.get('text', '')}")
    return "\n".join(lines) or (transcript or {}).get("text", "")


def _grade_once(client, checklist, text):
    resp = client.messages.create(
        model=pipeline.MODEL,
        max_tokens=pipeline.MAX_TOKENS,
        thinking={"type": "disabled"},
        system=[{"type": "text", "text": build_system_prompt(checklist),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_user_message(text)}],
    )
    raw = resp.content[0].text
    i, j = raw.find("{"), raw.rfind("}") + 1
    report = normalize_report(json.loads(raw[i:j]), checklist)
    u = resp.usage
    cost = (u.input_tokens * _IN_PER_M + u.output_tokens * _OUT_PER_M) / 1e6
    return report, {"input": u.input_tokens, "output": u.output_tokens,
                    "cache_read": getattr(u, "cache_read_input_tokens", 0),
                    "cost": cost}


def _agreement(values) -> float:
    """Fraction of runs that landed on the most common answer."""
    if not values:
        return 1.0
    return Counter(values).most_common(1)[0][1] / len(values)


def measure(internal_id, runs, workers) -> dict:
    conn = _connect()
    try:
        call = _load(conn.cursor(), internal_id)
    finally:
        conn.close()

    print(f"\n{'=' * 68}\n{internal_id}  ·  {call['org']}  ·  {call['duration']}s"
          f"  ·  stored verdict: {call['stored_verdict']}\n{'=' * 68}")
    print(f"grading {runs}× (transcript held fixed) …")

    client = anthropic.Anthropic(
        api_key=_ENV["ANTHROPIC_API_KEY"],
        timeout=pipeline.ANTHROPIC_TIMEOUT,
        max_retries=pipeline.ANTHROPIC_MAX_RETRIES,
    )

    def one(_):
        return _grade_once(client, call["checklist"], call["text"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, range(runs)))

    reports = [r for r, _ in results]
    spend = sum(u["cost"] for _, u in results)

    verdicts = [r["verdict"] for r in reports]
    v_agree = _agreement(verdicts)

    per_item = defaultdict(list)
    for r in reports:
        for sec in r.get("sections") or []:
            for item in sec.get("items") or []:
                per_item[(sec.get("name"), item.get("name"))].append(item.get("status"))
    item_agreements = {k: _agreement(v) for k, v in per_item.items()}
    unstable = sorted((a, k) for k, a in item_agreements.items() if a < 1.0)

    phrase_runs = [{(p["phrase"], p.get("timestamp")) for p in
                    (r["auto_fail_phrases"]["phrases"] or [])} for r in reports]
    phrase_counts = Counter(p for s in phrase_runs for p in s)
    flips = [r["program_flip"]["detected"] for r in reports]

    print(f"\n  verdicts            {Counter(verdicts).most_common()}")
    print(f"  verdict agreement   {v_agree:.0%}"
          + ("   ← two runs can disagree" if v_agree < 1 else "   (stable)"))
    if item_agreements:
        print(f"  item agreement      mean {statistics.mean(item_agreements.values()):.1%}"
              f"  ·  {len(unstable)} of {len(item_agreements)} requirements moved")
    for agree, (sec, name) in unstable[:8]:
        seen = Counter(per_item[(sec, name)]).most_common()
        print(f"      {agree:.0%}  {str(name)[:52]:52} {seen}")
    if phrase_counts:
        print("  phrases (runs seen in):")
        for (phrase, ts), n in phrase_counts.most_common():
            flag = "  ← intermittent" if n < runs else ""
            print(f"      {n}/{runs}  [{ts}] {phrase!r}{flag}")
    print(f"  program flip        {Counter(flips).most_common()}")
    print(f"  spend               ${spend:.2f}")

    return {
        "internal_id": internal_id, "org": call["org"], "duration": call["duration"],
        "runs": runs, "verdicts": verdicts, "verdict_agreement": v_agree,
        "item_agreement_mean": (statistics.mean(item_agreements.values())
                                if item_agreements else None),
        "unstable_items": [{"section": s, "item": n, "agreement": a,
                            "statuses": per_item[(s, n)]} for a, (s, n) in unstable],
        "phrase_frequency": {f"{p} @{t}": n for (p, t), n in phrase_counts.items()},
        "program_flip": flips,
        "spend_usd": round(spend, 4),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("internal_ids", nargs="*", help="call internal ids")
    ap.add_argument("--runs", type=int, default=3, help="gradings per call (default 3)")
    ap.add_argument("--workers", type=int, default=3, help="concurrent gradings")
    ap.add_argument("--all", action="store_true", help="every completed call")
    ap.add_argument("--max-duration", type=int, default=None,
                    help="skip calls longer than this many seconds")
    ap.add_argument("--out", default=None, help="write raw results as JSON")
    args = ap.parse_args()

    ids = args.internal_ids
    if args.all:
        conn = _connect(); cur = conn.cursor()
        cur.execute("""select distinct c.internal_id from calls c
                       join transcripts t on t.call_id=c.id
                       join reports r on r.call_id=c.id
                       where c.status='complete' and c.internal_id is not null
                       and (%s is null or c.duration <= %s)
                       order by c.internal_id""",
                    (args.max_duration, args.max_duration))
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
    if not ids:
        ap.error("give at least one internal id, or --all")

    results = [measure(i, args.runs, args.workers) for i in ids]

    print(f"\n{'=' * 68}\nSUMMARY\n{'=' * 68}")
    unstable_calls = [r for r in results if r["verdict_agreement"] < 1.0]
    print(f"  calls measured            {len(results)}  ({args.runs} runs each)")
    print(f"  verdict unstable on       {len(unstable_calls)} of {len(results)}")
    means = [r["item_agreement_mean"] for r in results if r["item_agreement_mean"]]
    if means:
        print(f"  mean item agreement       {statistics.mean(means):.1%}")
    worst = sorted(((i["agreement"], i["item"], r["internal_id"])
                    for r in results for i in r["unstable_items"]))[:10]
    if worst:
        print("  least stable requirements across all calls:")
        for a, name, iid in worst:
            print(f"      {a:.0%}  {str(name)[:52]:52} ({iid})")
    print(f"  total spend               ${sum(r['spend_usd'] for r in results):.2f}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"\n  raw results → {args.out}")


if __name__ == "__main__":
    main()
