"""What a recording's filename already tells us.

Dialers name their exports predictably, and the ones this product sees carry
the date and the client's number in the filename. Asking a person to retype
those — for every file, on a bulk import — is asking them to do work a regex
does perfectly.

Every real filename in the database so far:

    20260810-172905_3616522851-all.mp3
    20260126-075444_5679_3134082479-all.mp3
    20260126111735_6820_919513097039-all.mp3
    20260807-142611_2074769156-all.mp3

So: YYYYMMDD, optionally a dash, HHMMSS, then either the client's number or an
agent extension followed by the client's number, then `-all`.

Nothing here guesses. A field that cannot be read with confidence is returned
as None so the form asks for it, because a wrong date silently attached to a
compliance record is worse than an empty one.
"""
from __future__ import annotations

import re
from datetime import date

# YYYYMMDD[-]HHMMSS then one or two underscore-separated number groups.
_PATTERN = re.compile(
    r"""^
    (?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})   # date
    -?
    (?P<time>\d{6})?                       # time, sometimes absent
    (?:_(?P<ext>\d{2,6}))?                 # agent extension, sometimes present
    _(?P<phone>\d{7,15})                   # the client's number
    """,
    re.VERBOSE,
)

# North American numbers arrive both bare and with a country prefix. Ten digits
# is the form the rest of the app displays, so 1- and 91- prefixes are trimmed
# back to it — but only when what remains is exactly ten, never by truncating.
_TRIM_PREFIXES = ("1", "91")


def normalise_phone(digits: str | None) -> str | None:
    if not digits:
        return None
    d = re.sub(r"\D", "", digits)
    for p in _TRIM_PREFIXES:
        if len(d) == 10 + len(p) and d.startswith(p):
            d = d[len(p):]
            break
    return d if 7 <= len(d) <= 15 else None


def parse_filename(filename: str) -> dict:
    """Return {call_date, client_phone, agent_ext}, any of which may be None."""
    out = {"call_date": None, "client_phone": None, "agent_ext": None}
    if not filename:
        return out

    stem = filename.rsplit("/", 1)[-1]
    m = _PATTERN.match(stem)
    if not m:
        return out

    try:
        d = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError:
        d = None                       # 20261399 is not a date; ask instead
    else:
        # A recording dated in the future is a filename that does not mean what
        # it looks like. Rather than attach it, leave it for a person.
        if d > date.today():
            d = None

    out["call_date"] = d
    out["client_phone"] = normalise_phone(m.group("phone"))
    out["agent_ext"] = m.group("ext")
    return out


def describe(parsed: dict) -> str:
    """One short line for the review step: what we read, in plain words."""
    bits = []
    if parsed.get("call_date"):
        bits.append(parsed["call_date"].strftime("%b %-d, %Y"))
    if parsed.get("client_phone"):
        bits.append(parsed["client_phone"])
    return " · ".join(bits) or "Nothing readable in the filename"
