"""
Written-warning document generation.

Loads a completed analysis from the DB, applies any manual overrides,
and asks Claude for the two call-specific finding-bullet bodies (with
inline transcript timestamps). Builds a .docx that mirrors the BDS
Written Warning template (boxes/checkboxes/signatures): the two
AI-generated narrative bullets are the only call-specific content;
everything else is fixed template copy.

Everything outside the highlighted fields in the source template is
fixed copy and must not be changed without explicit approval.
"""

import io
import os
import json
import logging
import re
import sqlite3

import anthropic
from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, Inches, RGBColor

logger = logging.getLogger('call-qa-tool.writeup')

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'bds_logo.png')


# ---------------------------------------------------------------------------
# Claude call to produce the two prose finding bodies
# ---------------------------------------------------------------------------

_WRITEUP_SYSTEM_PROMPT = """You produce two short paragraphs for a written warning at Better Debt Solutions.

You will be given:
- The agent's name
- The full transcript of the call (with [MM:SS] or [HH:MM:SS] speaker timestamps)
- The QA analysis JSON (items not covered, high-risk phrases detected, etc.)

Write TWO paragraphs matching this example tone and structure:

EXAMPLE 1 — Failure to Accurately Explain Program / Client Misguidance:
"{Agent} mischaracterized the program at [12:34], failing to correct or properly explain key components, including stop payments to creditors, implying our company will be making payments, account delinquency, potential closures, and negative credit reporting."

EXAMPLE 2 — Failure to Disclose Risks / Ensure Understanding:
"{Agent} failed to properly disclose material program risks at [23:15], including potential litigation, tax implications, and negative credit impact, and did not ensure the client had an accurate understanding of the program."

Rules:
- Each paragraph is 1-3 sentences. Concise, formal HR/compliance tone.
- Start with the agent's literal name (no braces, no "the agent").
- Cite at least one transcript timestamp inline using square brackets, e.g. "at [12:34]". Use timestamps drawn from the analysis JSON or the transcript — do not invent them.
- Reference SPECIFIC failures from this call. If a category has no clear failures in this call, write a single short sentence stating the agent met expectations in that area (no timestamp needed in that case).
- Do not invent failures that aren't supported by the transcript or analysis.
- No long quotes. No bullet lists inside the paragraph.

BDS WORKFLOW CONTEXT — important when writing the misguidance bullet:
- The BDS call begins as a personal loan application. The agent IS expected to refer to "the loan" / "a personal loan" during the discovery / pre-underwriting phase before the loan decline / pivot occurs. Loan vocabulary BEFORE the pivot is acceptable and must NOT be cited as misguidance.
- Only loan vocabulary used AFTER the pivot / approval is a violation.
- If the agent skipped the loan decline pivot entirely, the failure to cite is "the agent did not perform the loan decline pivot" — do NOT quote pre-pivot loan references as the supporting evidence for that bullet. Cite the missing pivot itself, with an approximate timestamp drawn from where the pivot should have occurred (typically right after the agent returns from the underwriting hold).

Output STRICT JSON, no prose around it:
{
  "misguidance_body": "...",
  "risk_disclosure_body": "..."
}
"""


def generate_finding_bodies(claude_client, agent_name, transcript, results, model="claude-opus-4-6"):
    user_msg = (
        f"AGENT NAME: {agent_name}\n\n"
        f"QA ANALYSIS JSON:\n{json.dumps(results, indent=2)}\n\n"
        f"FULL TRANSCRIPT:\n{transcript}\n"
    )
    response = claude_client.messages.create(
        model=model,
        max_tokens=1200,
        system=_WRITEUP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text
    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start == -1 or end <= start:
        raise ValueError(f"Claude write-up response had no JSON: {raw[:200]!r}")
    payload = json.loads(raw[start:end])
    return payload['misguidance_body'], payload['risk_disclosure_body']


# ---------------------------------------------------------------------------
# .docx construction
#
# The write-up uses the standardized two-bullet BDS template. The derived
# finding lists (approval/post-enrollment items not covered, high-risk phrases)
# were intentionally removed from the document; the two narrative bullets above
# are AI-generated and call-specific.
# ---------------------------------------------------------------------------

def _set_cell_borders(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_borders = OxmlElement('w:tcBorders')
    for side in ('top', 'left', 'bottom', 'right'):
        b = OxmlElement(f'w:{side}')
        b.set(qn('w:val'), 'single')
        b.set(qn('w:sz'), '4')
        b.set(qn('w:color'), '000000')
        tc_borders.append(b)
    tc_pr.append(tc_borders)


def _add_runs(paragraph, segments):
    """segments: list of (text, bold) tuples."""
    for text, bold in segments:
        run = paragraph.add_run(text)
        run.bold = bold


def _set_paragraph_spacing(paragraph, space_before_pt=0, space_after_pt=4, line_spacing=1.15):
    pf = paragraph.paragraph_format
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)
    pf.line_spacing = line_spacing


def _build_header_table(doc, agent_name, mode='written'):
    table = doc.add_table(rows=6, cols=3)
    table.style = 'Table Grid'
    table.autofit = False

    row0 = table.rows[0].cells
    row0[1].merge(row0[2])
    _add_runs(row0[0].paragraphs[0], [('Employee Name: ', True), (agent_name, False)])
    _add_runs(row0[1].paragraphs[0], [('Job Title: ', True), ('Sr Debt Advisor', False)])

    row1 = table.rows[1].cells
    row1[1].merge(row1[2])
    _add_runs(row1[0].paragraphs[0], [('Date of Hire: ', True)])
    _add_runs(row1[1].paragraphs[0], [('Department: ', True), ('Sales', False)])

    row2 = table.rows[2].cells
    row2[1].merge(row2[2])
    _add_runs(row2[0].paragraphs[0], [('Date of Counseling: ', True)])
    _add_runs(row2[1].paragraphs[0], [('Supervisor/Manager: ', True)])

    # Verbal warning pre-marks Oral Discussion + First Warning per the template.
    if mode == 'verbal':
        row3_labels = ('_x_ Oral Discussion', '_x_ First Warning', '____ Second Warning')
    else:
        row3_labels = ('___ Oral Discussion', '____ First Warning', '____ Second Warning')
    row3 = table.rows[3].cells
    for cell, label in zip(row3, row3_labels):
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell.paragraphs[0].add_run(label).bold = True

    row4 = table.rows[4].cells
    for cell, label in zip(row4, ('____ Final Warning', '___ Termination Decision', '__ PIP')):
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell.paragraphs[0].add_run(label).bold = True

    row5 = table.rows[5].cells
    row5[0].merge(row5[1]).merge(row5[2])
    purpose_noun = 'warning' if mode == 'verbal' else 'meeting'
    row5[0].paragraphs[0].add_run(f'The purpose of this {purpose_noun} is to address:')

    purpose_row = table.add_row().cells
    purpose_row[1].merge(purpose_row[2])
    purpose_row[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    purpose_row[0].paragraphs[0].add_run('___ Performance Conduct')
    purpose_row[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    purpose_row[1].paragraphs[0].add_run('____ Policy/Procedure Violation')

    for row in table.rows:
        for cell in row.cells:
            _set_cell_borders(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def _add_logo_or_placeholder(doc, width_inches=2.2):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if os.path.exists(LOGO_PATH):
        run = p.add_run()
        run.add_picture(LOGO_PATH, width=Inches(width_inches))
    else:
        run = p.add_run('[BDS LOGO]')
        run.italic = True
        run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)


def _add_main_bullet(doc, heading, body_text):
    p = doc.add_paragraph(style='List Bullet')
    _add_runs(p, [(heading, True), (' ', True), (body_text, False)])
    _set_paragraph_spacing(p, space_after_pt=6)
    return p


def build_writeup_docx(agent_name, internal_id, call_date,
                       misguidance_body, risk_disclosure_body, mode='written'):
    doc = Document()

    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)
    for section in doc.sections:
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # ---- Page 1 ----
    _add_logo_or_placeholder(doc, width_inches=2.0)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('DOCUMENTED VERBAL WARNING' if mode == 'verbal' else 'WRITTEN WARNING')
    run.bold = True
    run.font.size = Pt(14)

    _build_header_table(doc, agent_name, mode=mode)

    doc.add_paragraph()  # spacer

    intro = doc.add_paragraph()
    intro.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # Both the internal ID and the call date are optional on upload, so each
    # clause is omitted rather than printing "file # , dated ,".
    opening = [('Following a review of a client call', False)]
    if internal_id:
        opening += [(', file # ', False), (str(internal_id), False)]
    if call_date:
        opening += [(', dated ', False), (call_date, False)]
    opening += [
        (', Agent failed to properly disclose missed payments to the client which is in '
         'direct violation of company compliance policies and procedures and regulatory standards. '
         'Failure to properly complete this critical step invalidates the informed consent and '
         'exposes the company to significant regulatory and legal risks.', False),
    ]
    _add_runs(intro, opening)
    _set_paragraph_spacing(intro, space_after_pt=8)

    p = doc.add_paragraph()
    p.add_run('Findings include:')
    _set_paragraph_spacing(p, space_after_pt=4)

    # Bullet 1 — Misguidance
    _add_main_bullet(doc,
                     'Failure to Accurately Explain Program / Client Misguidance:',
                     misguidance_body)

    # Bullet 2 — Risk disclosure
    _add_main_bullet(doc,
                     'Failure to Disclose Risks / Ensure Understanding:',
                     risk_disclosure_body)

    # Corrective Action follows the findings directly (no forced page break)
    # so short calls don't leave a large blank gap at the bottom of page 1;
    # content reflows onto page 2 only when it actually runs out of room.
    ca_heading = doc.add_paragraph()
    ca_heading.add_run('Corrective Action').bold = True
    _set_paragraph_spacing(ca_heading, space_before_pt=10, space_after_pt=4)
    ca_heading.paragraph_format.keep_with_next = True

    corrective_items = [
        'Strictly adhere to all company policies and procedures, including but not limited to all '
        'compliance policies, procedures, and regulatory compliance standards.',
        'Follow the program approval and post-enrollment scripts exactly as instructed.',
        'Provide clients with compliant, accurate, and complete disclosures of the program.',
        'Cease any misleading or prohibited language during all client interactions.',
        'Seek guidance from management immediately when needed.',
        'Attend one-on-one coaching with the manager to obtain a clear understanding of the job '
        'expectations.',
    ]
    for item in corrective_items:
        b = doc.add_paragraph(style='List Bullet')
        b.add_run(item)
        _set_paragraph_spacing(b, space_after_pt=3)

    # The closing monitoring paragraph, employee comments, signature lines and
    # disclaimer appear only on the WRITTEN warning. The documented verbal
    # warning ends after the Corrective Action items (per its template).
    if mode != 'verbal':
        closing = doc.add_paragraph()
        closing.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        closing.add_run(
            'Your progress on the above corrective action items requiring improvement will be closely '
            'monitored. Improvement must begin immediately and be maintained. If no improvement in any '
            'and all of the above areas is noticed at any time following this corrective write-up, '
            'further disciplinary action up to and including termination may occur.'
        )
        _set_paragraph_spacing(closing, space_after_pt=10)

        p = doc.add_paragraph()
        p.add_run('Employee’s Comments:').bold = True
        # Keep the underscore count within the text column so it stays a single
        # line — too many wraps to a short stub on the next line.
        doc.add_paragraph('_' * 80)

        sig_spacer = doc.add_paragraph()
        _set_paragraph_spacing(sig_spacer, space_after_pt=2)

        sig1 = doc.add_paragraph()
        _add_runs(sig1, [
            ('Employee Signature*', True),
            ('________________________   ', False),
            ('Date:', True),
            ('___________________', False),
        ])
        _set_paragraph_spacing(sig1, space_after_pt=8)

        sig2 = doc.add_paragraph()
        _add_runs(sig2, [
            ('Manager’s Signature ', True),
            ('________________________   ', False),
            ('Date:', True),
            ('___________________', False),
        ])
        _set_paragraph_spacing(sig2, space_after_pt=14)

        disclaimer = doc.add_paragraph()
        disclaimer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = disclaimer.add_run(
            '*It is understood that the employee’s signature indicates the information has been '
            'discussed and does not necessarily Indicate agreement*'
        )
        r.italic = True

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Top-level entry point used by the Flask route
# ---------------------------------------------------------------------------

