"""Re-grade one call from its stored transcript.

For use after a prompt or normalizer change, when an existing report was
produced by logic that has since been fixed.

Deliberately does not re-transcribe: the audio has not changed, AssemblyAI
charges by the hour, and holding the transcript fixed is what makes the
before/after comparison mean anything. Mirrors the pipeline's analysis step
exactly — same model, same cache markers, same normalizer.

Dry run by default. Pass --write to save.

    python3 scripts/regrade_call.py 888454
    python3 scripts/regrade_call.py 888454 --write

Grading is not perfectly repeatable — see scripts/measure_variance.py. Run that
first if you want to know how much a single re-grade can be trusted.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import dotenv_values
v = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
os.environ.update({k: x for k, x in v.items() if x})

import anthropic, psycopg2
import pipeline
from prompt_builder import build_system_prompt, build_user_message
from report_normalizer import normalize_report

if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
    sys.exit("usage: regrade_call.py <internal_id> [--write]")
INTERNAL_ID = sys.argv[1]
conn = psycopg2.connect(v["DATABASE_URL"]); conn.autocommit = False
cur = conn.cursor()

cur.execute("""select c.id, c.compliance_profile_id, t.raw_transcript_json,
                      r.verdict, r.pass_fail_status
               from calls c
               join transcripts t on t.call_id = c.id
               left join reports r on r.call_id = c.id
               where c.internal_id = %s""", (INTERNAL_ID,))
row = cur.fetchone()
call_id, profile_id, transcript, old_verdict, _ = row
print(f"call {call_id[:8]}…  current verdict: {old_verdict}")

cur.execute("select script_sections_json from compliance_profiles where id = %s", (profile_id,))
profile_data = cur.fetchone()[0]

utts = transcript.get("utterances") or []
lines = []
for u in utts:
    ts = (u.get("start") or 0) // 1000
    lines.append(f"[{ts//60:02d}:{ts%60:02d}] {u.get('speaker','?')}: {u.get('text','')}")
transcript_text = "\n".join(lines) or transcript.get("text", "")
print(f"transcript: {len(utts)} utterances, {len(transcript_text):,} chars")

client = anthropic.Anthropic(api_key=v["ANTHROPIC_API_KEY"],
                             timeout=pipeline.ANTHROPIC_TIMEOUT,
                             max_retries=pipeline.ANTHROPIC_MAX_RETRIES)
print(f"grading with {pipeline.MODEL} …")
resp = client.messages.create(
    model=pipeline.MODEL,
    max_tokens=pipeline.MAX_TOKENS,
    thinking={"type": "disabled"},
    system=[{"type": "text", "text": build_system_prompt(profile_data),
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": build_user_message(transcript_text)}],
)
u = resp.usage
print(f"  tokens in={u.input_tokens} out={u.output_tokens} "
      f"cache_read={getattr(u,'cache_read_input_tokens',0)}")

raw = resp.content[0].text
i, j = raw.find("{"), raw.rfind("}") + 1
model_output = json.loads(raw[i:j])
report = normalize_report(model_output, profile_data)

print(f"\nnew verdict: {report['verdict']}  ({report['final_determination']})")
af = report["auto_fail_phrases"]
print(f"  phrases: {len(af['phrases'])} total — {af.get('agent_count')} agent, {af.get('client_count')} client")
for p in af["phrases"]:
    print(f"    [{p['timestamp']}] {p['phrase']!r} — spoken_by={p['spoken_by']!r} ({p['speaker']})")
flip = report["program_flip"]
print(f"  program_flip: {flip['detected']} — {flip['reason'][:150]}")
print(f"  ineligible_accounts: {len(report['ineligible_accounts'])}")
for a in report["ineligible_accounts"]:
    print(f"    {a['reason_code']}: {a['account']} [{a['timestamp']}] “{a['quote'][:80]}”")

if "--write" in sys.argv:
    cur.execute("""update reports set report_json=%s, verdict=%s,
                          pass_fail_status=%s where call_id=%s""",
                (json.dumps(report), report["verdict"],
                 report["final_determination"], call_id))
    conn.commit()
    print("\nreport updated in the database")
else:
    conn.rollback()
    print("\n(dry run — pass --write to save)")
conn.close()
