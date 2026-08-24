"""
Builds Claude system prompt and user message from a compliance profile dict.

The profile dict shape (from compliance_profiles.script_sections_json):
{
    "sections": [
        {
            "name": "Approval Script",
            "key": "approval_script",
            "items": [
                {"name": "...", "required": true, "notes": "..."},
                ...
            ]
        },
        ...
    ],
    "auto_fail_phrases": [
        {"phrase": "...", "description": "..."},
        ...
    ],
    # Optional industry extras. Debt-settlement templates set these; solar /
    # blank / others leave them off so horizontal orgs are not graded against
    # debt-relief program-flip logic.
    "grader_extensions": ["program_flip", "ineligible_accounts"]
}
"""

# Known extension ids. Anything else in the profile list is ignored.
PROGRAM_FLIP = "program_flip"
INELIGIBLE_ACCOUNTS = "ineligible_accounts"
KNOWN_EXTENSIONS = frozenset({PROGRAM_FLIP, INELIGIBLE_ACCOUNTS})


def grader_extensions(profile: dict | None) -> frozenset[str]:
    """Which informational grader extras this checklist enables."""
    raw = (profile or {}).get("grader_extensions") or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x) for x in raw if str(x) in KNOWN_EXTENSIONS)


def build_system_prompt(profile: dict) -> str:
    extensions = grader_extensions(profile)
    lines = [
        "You are a QA compliance analyst. You analyze call transcripts to verify that "
        "the agent communicated all required disclosures and avoided prohibited phrases.",
        "",
        "You will receive a call transcript with speaker labels and timestamps.",
        "Your task:",
        "1. Identify which speaker is the agent and which is the client based on context.",
        "2. Check each required item against what the AGENT said (not the client).",
        "3. Scan the transcript for auto-fail phrases.",
        "4. Return a structured JSON report.",
        "",
    ]

    sections = profile.get("sections", [])
    for section in sections:
        name = section.get("name", "Section")
        items = section.get("items", [])
        req_count = sum(1 for i in items if i.get("required", True))
        lines += [
            "─" * 60,
            f"{name.upper()} — REQUIRED ITEMS ({req_count} required)",
            "─" * 60,
            "",
        ]
        for idx, item in enumerate(items, 1):
            item_name = item.get("name", "")
            notes = item.get("notes", "")
            required = item.get("required", True)
            suffix = "" if required else " (OPTIONAL — only required if agent brings it up)"
            note_part = f" — {notes}" if notes else ""
            lines.append(f"{idx}. {item_name}{suffix}{note_part}")
        lines.append("")

    phrases = profile.get("auto_fail_phrases", [])
    if phrases:
        lines += [
            "─" * 60,
            "AUTO-FAIL PHRASES — flag the call as CRITICAL FAIL if ANY appear",
            "─" * 60,
            "",
        ]
        for p in phrases:
            phrase = p.get("phrase", "")
            desc = p.get("description", "")
            desc_part = f" — {desc}" if desc else ""
            lines.append(f'- "{phrase}"{desc_part}')
        lines.append("")

    # Build the section output schema dynamically
    section_schema_parts = []
    for section in sections:
        key = section.get("key") or section.get("name", "section").lower().replace(" ", "_")
        name = section.get("name", "Section")
        section_schema_parts.append(
            f'    {{\n'
            f'      "key": "{key}",\n'
            f'      "name": "{name}",\n'
            f'      "covered_count": <number>,\n'
            f'      "total_count": <number of required items>,\n'
            f'      "items": [\n'
            f'        {{\n'
            f'          "name": "<item name>",\n'
            f'          "status": "covered" or "not_covered",\n'
            f'          "timestamp": "<MM:SS or HH:MM:SS, or null if not found>",\n'
            f'          "evidence": "<brief quote if covered, or note on what was missing>"\n'
            f'        }}\n'
            f'      ]\n'
            f'    }}'
        )
    sections_schema = ",\n".join(section_schema_parts) if section_schema_parts else "    { ... }"

    lines += [
        "─" * 60,
        "RULES",
        "─" * 60,
        "",
        "- Only credit the AGENT. Do not count things the client said.",
        "- Paraphrasing is acceptable if the full meaning is communicated.",
        "- 'Almost said it' is NOT coverage. Incomplete or hedged disclosures = not_covered.",
        "- Optional items: if the agent never mentions the topic, mark covered.",
        "  They only become required if the agent brings up the topic.",
        "- total_count in each section = number of required items (not optional).",
        "- You MUST return a status for every required item. Omitting an item is worse",
        "  than marking not_covered — the report will be marked incomplete and re-run.",
        "",
        "─" * 60,
        "OUTPUT FORMAT — return ONLY valid JSON, no markdown, no code fences",
        "─" * 60,
        "",
        "{",
        '  "sections": [',
        sections_schema,
        "  ],",
        '  "auto_fail_phrases": {',
        '    "detected": <boolean>,',
        '    "phrases": [',
        '      {',
        '        "phrase": "<the phrase found>",',
        '        "timestamp": "<timestamp>",',
        '        "speaker": "<Speaker label>",',
        '        "spoken_by": "agent" | "client",',
        '        "is_violation": <boolean — false if the phrase is negated, disclaimed or part of a required disclosure>,',
        '        "quote": "<exact quote from transcript>",',
        '        "violation": "<which auto-fail rule this matches>"',
        '      }',
        '    ]',
        "  },",
    ]

    if PROGRAM_FLIP in extensions:
        lines += [
            '  "program_flip": {',
            '    "detected": <boolean>,',
            '    "reason": "<one sentence: what suggests it, or why not>",',
            '    "evidence": [',
            '      {',
            '        "timestamp": "<timestamp>",',
            '        "speaker": "<Speaker label>",',
            '        "quote": "<exact quote from transcript>"',
            '      }',
            '    ]',
            "  },",
        ]

    if INELIGIBLE_ACCOUNTS in extensions:
        lines += [
            '  "ineligible_accounts": [',
            '    {',
            '      "reason_code": "prior_settlement" | "secured_vehicle" | "litigation",',
            '      "account": "<the account as the client described it>",',
            '      "timestamp": "<timestamp>",',
            '      "speaker": "<Speaker label>",',
            '      "quote": "<exact quote from transcript>",',
            '      "note": "<one sentence: what makes it ineligible>"',
            '    }',
            "  ],",
        ]

    lines += [
        '  "final_determination": "PASS" or "FAIL — <Section Name>" or "FAIL — Both" or "CRITICAL FAIL",',
        '  "summary": "<2-3 sentence plain-English summary of the findings>"',
        "}",
        "",
        "ASSERTED, NOT MERELY SAID — check this before flagging anything:",
        "  A prohibited phrase counts only when the agent ASSERTS it as true of",
        "  this program / offer. The same words inside a denial, a disclaimer or a",
        "  required disclosure are the agent doing their job, and flagging them",
        "  inverts the meaning of the call.",
        "",
        "  These are NOT violations:",
        '    "this program is not a 0% payment plan"',
        '    "I do want to inform you, it\'s not guaranteed"',
        '    "we cannot guarantee any particular result"',
        '    "this is not a consolidation loan"',
        "  Compliance scripts often include such disclaimers word for word —",
        "  that is what makes them mandatory, and it is exactly why the literal",
        "  words appear.",
        "",
        "  These ARE violations, because the agent asserts the claim:",
        '    "your interest is going to be 0%"',
        '    "this is guaranteed to work"',
        '    "we guarantee your creditors will settle"',
        "",
        "  Read the whole sentence, and the sentence before it, before deciding.",
        "  Report the occurrence either way — a reviewer may want to see that",
        "  the disclosure was read — but set is_violation = false when the agent",
        "  is negating, qualifying or disclosing. Only is_violation = true can",
        "  fail a call.",
        "",
        "WHO SAID IT — this decides the verdict:",
        "  `spoken_by` must be \"agent\" or \"client\" for every phrase.",
        "  Only the AGENT can fail a call. This tool grades the agent, and a",
        "  client using a prohibited phrase to describe their own situation is",
        "  not misconduct by the person being reviewed.",
        "  Still report a client-spoken phrase, with spoken_by = \"client\" —",
        "  a reviewer may want to know the framing was used and the agent did",
        "  not correct it. Mark it \"agent\" if the agent adopts, repeats or",
        "  affirms it.",
        "",
    ]

    if PROGRAM_FLIP in extensions:
        lines += [
            "PROGRAM FLIP — informational only:",
            "  Set program_flip.detected = true if the client indicates they are",
            "  already enrolled in, or were previously enrolled in, another debt",
            "  relief, settlement, consolidation or credit repair program — or that",
            "  accounts being enrolled on this call were already in such a program.",
            "  Name the other company in `reason` when the client names it.",
            "  Be conservative: a client merely naming a creditor, a bank or an",
            "  ordinary loan is NOT a program flip. If it is ambiguous, set detected",
            "  = true and say plainly in `reason` what is unclear — the report",
            "  labels this as *possible*, and a reviewer decides.",
            "  This NEVER changes any item's status and NEVER changes the",
            "  determination. It is context for the human, nothing more. A flip is",
            "  not a violation; it changes which disclosures are meaningful, and",
            "  that judgment belongs to the reviewer.",
            "",
        ]

    if INELIGIBLE_ACCOUNTS in extensions:
        lines += [
            "INELIGIBLE ACCOUNTS — informational only:",
            "  List an account when the CLIENT says something that makes it",
            "  ineligible for enrolment:",
            "    prior_settlement — already settled on that account through another",
            "      company",
            "    secured_vehicle  — the debt is secured against a vehicle (car loan,",
            "      auto title)",
            "    litigation       — they have already been sued on that account, or",
            "      there is a judgment against them for it",
            "",
            "  Two distinctions matter more than anything else here, because getting",
            "  them wrong produces confident false alarms that make the whole report",
            "  untrustworthy:",
            "",
            "  1. The AGENT ASKING is not a finding. Agents are expected to screen",
            "     for these — \"are there any active lawsuits or judgments?\" is the",
            "     script working. Only the CLIENT ANSWERING YES creates an entry. If",
            "     the client says no, or does not answer, there is nothing to list.",
            "",
            "  2. An account discussed as a monthly EXPENSE is not an enrolled",
            "     account. Car payments, insurance and mortgages routinely come up",
            "     during a budget review; that is not the same as enrolling the debt.",
            "     Only flag a vehicle debt if it appears to be among the accounts",
            "     being enrolled.",
            "",
            "  Quote the client, not the agent, in `quote`. Return an empty list when",
            "  nothing qualifies — an empty list is the common and expected answer.",
            "  These NEVER change an item's status and NEVER change the",
            "  determination. Whether an ineligible account was actually enrolled is",
            "  for the reviewer to confirm.",
            "",
        ]

    lines += [
        "DETERMINATION LOGIC:",
        "- PASS — all required items covered in every section, no auto-fail phrases detected",
        "- FAIL — <Section Name> — one or more required items missing from that section only",
        "- FAIL — Both — required items missing from two or more sections",
        "- CRITICAL FAIL — at least one auto-fail phrase spoken BY THE AGENT",
        "  (overrides everything). Client-spoken phrases never cause this.",
    ]

    return "\n".join(lines)


def build_user_message(transcript_text: str) -> str:
    sep = "─" * 60
    return (
        f"Analyze the following call transcript against the compliance checklist above.\n\n"
        f"TRANSCRIPT:\n{sep}\n{transcript_text}\n{sep}\n\n"
        f"Review the full transcript carefully. Identify agent vs client, "
        f"check every required item, scan for auto-fail phrases, and return the JSON report."
    )
