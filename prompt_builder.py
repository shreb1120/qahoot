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
    ]
}
"""


def build_system_prompt(profile: dict) -> str:
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
        '        "quote": "<exact quote from transcript>",',
        '        "violation": "<which auto-fail rule this matches>"',
        '      }',
        '    ]',
        "  },",
        '  "final_determination": "PASS" or "FAIL — <Section Name>" or "FAIL — Both" or "CRITICAL FAIL",',
        '  "summary": "<2-3 sentence plain-English summary of the findings>"',
        "}",
        "",
        "DETERMINATION LOGIC:",
        "- PASS — all required items covered in every section, no auto-fail phrases detected",
        "- FAIL — <Section Name> — one or more required items missing from that section only",
        "- FAIL — Both — required items missing from two or more sections",
        "- CRITICAL FAIL — at least one auto-fail phrase detected (overrides everything)",
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
