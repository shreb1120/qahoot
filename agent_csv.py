"""Parse an agent roster CSV.

Pure: no Flask, no database, fully unit-testable — the same shape as
report_normalizer.py.

Customers export from Excel, Google Sheets and whatever their dialler produces,
so the input is genuinely unpredictable: BOMs, CRLF, semicolon delimiters from
European locales, an .xlsx renamed to .csv, cp1252 accents. Each of those has a
specific message rather than a generic failure, because "invalid file" tells an
admin nothing about what to do next.

Two rules the caller depends on:

* A fatal problem (`error` set) means **nothing** is imported. A partial import
  of a file the user misunderstood is worse than a clear refusal.
* Names are returned exactly as typed apart from whitespace collapsing. Never
  title-case: McDonald, de la Cruz and O'Brien all lose.
"""
from __future__ import annotations

import csv
import io
import re

MAX_BYTES = 1_048_576      # 1 MB
MAX_ROWS = 5_000
MAX_NAME = 120             # matches the manual add field; well under String(255)

# A first row is a header if any cell looks like one.
_HEADER_CELLS = {
    "first", "first name", "firstname", "given name", "given",
    "last", "last name", "lastname", "surname", "family name",
    "name", "full name", "agent", "agent name", "email", "e-mail",
}
_FIRST = {"first", "first name", "firstname", "given name", "given"}
_LAST = {"last", "last name", "lastname", "surname", "family name"}
_FULL = {"name", "full name", "agent", "agent name"}

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _clean(cell: str) -> str:
    """Collapse all whitespace runs, including tabs and non-breaking spaces."""
    return " ".join((cell or "").replace(" ", " ").split())


def _decode(raw: bytes) -> tuple[str | None, str | None]:
    if raw[:4] == b"PK\x03\x04":
        return None, ("That looks like an Excel workbook (.xlsx). In Excel choose "
                      "File → Save As → CSV UTF-8, then upload that file.")
    if b"\x00" in raw[:4096]:
        return None, "That doesn't look like a CSV file."
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    # Deliberately no latin-1 fallback: it decodes any byte sequence, so it
    # cannot fail and would silently mangle names instead of asking.
    return None, ("We couldn't read that file's text encoding. Re-save it as "
                  "CSV UTF-8 and try again.")


def _dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _column_indexes(header: list[str]) -> tuple[int | None, int | None, int | None]:
    """(first, last, full) column positions, located by header name."""
    first = last = full = None
    for i, cell in enumerate(header):
        c = _clean(cell).lower()
        if first is None and c in _FIRST:
            first = i
        elif last is None and c in _LAST:
            last = i
        elif full is None and c in _FULL:
            full = i
    return first, last, full


def parse_agent_csv(raw: bytes) -> dict:
    """Return {names, invalid, invalid_rows, error}.

    `names` preserves file order. `invalid_rows` holds the first few 1-based
    line numbers so the flash message can point at them; counts alone are not
    actionable.
    """
    result: dict = {"names": [], "invalid": 0, "invalid_rows": [], "error": None}

    if not raw:
        result["error"] = "That file is empty."
        return result
    if len(raw) > MAX_BYTES:
        result["error"] = (f"That file is larger than {MAX_BYTES // 1024} KB. "
                           "Agent rosters should be far smaller — check you picked "
                           "the right file.")
        return result

    text, error = _decode(raw)
    if error:
        result["error"] = error
        return result

    reader = csv.reader(io.StringIO(text, newline=""), _dialect(text[:4096]))
    try:
        rows = list(reader)
    except csv.Error as exc:
        result["error"] = f"We couldn't read that CSV ({exc})."
        return result

    # Drop entirely blank rows up front; they are formatting, not data.
    numbered = [(n, r) for n, r in enumerate(rows, start=1) if any(_clean(c) for c in r)]
    if not numbered:
        result["error"] = "That file has no rows in it."
        return result

    first_idx = last_idx = full_idx = None
    header_line, header_row = numbered[0]
    if any(_clean(c).lower() in _HEADER_CELLS for c in header_row):
        first_idx, last_idx, full_idx = _column_indexes(header_row)
        numbered = numbered[1:]
        if first_idx is None and last_idx is None and full_idx is None:
            # A header we recognised but no usable name column, e.g. only "email".
            result["error"] = ("We couldn't find a name column. Use headers like "
                               "First,Last or a single Name column.")
            return result

    if len(numbered) > MAX_ROWS:
        result["error"] = (f"That file has {len(numbered):,} rows; the limit is "
                           f"{MAX_ROWS:,}. Split it and import in batches.")
        return result

    for line_no, row in numbered:
        cells = [_clean(c) for c in row]
        if full_idx is not None:
            name = cells[full_idx] if full_idx < len(cells) else ""
        elif first_idx is not None or last_idx is not None:
            fn = cells[first_idx] if first_idx is not None and first_idx < len(cells) else ""
            ln = cells[last_idx] if last_idx is not None and last_idx < len(cells) else ""
            name = f"{fn} {ln}".strip()
        elif len(cells) == 1:
            name = cells[0]
        else:
            name = f"{cells[0]} {cells[1]}".strip()

        name = " ".join(name.split())
        if not name or len(name) > MAX_NAME or not _HAS_LETTER.search(name):
            result["invalid"] += 1
            if len(result["invalid_rows"]) < 3:
                result["invalid_rows"].append(line_no)
            continue
        result["names"].append(name)

    return result


def dedupe_key(name: str) -> str:
    """Case- and whitespace-insensitive identity for a roster name."""
    return " ".join((name or "").split()).casefold()
