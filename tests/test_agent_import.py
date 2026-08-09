"""Agent roster CSV import.

Customers export from Excel, Sheets and their dialler, so the parser tests are
the ones that matter: the file format is the least controlled input the product
accepts.
"""
import io
import pytest
from agent_csv import MAX_NAME, MAX_ROWS, dedupe_key, parse_agent_csv


def names(raw: bytes):
    return parse_agent_csv(raw)["names"]


# ── Parser ────────────────────────────────────────────────────────────────────

def test_header_row_is_detected_and_skipped():
    assert names(b"First,Last\nJose,Alvarez\nDana,Whitfield\n") == ["Jose Alvarez", "Dana Whitfield"]


def test_a_file_with_no_header_is_positional():
    assert names(b"Jose,Alvarez\nDana,Whitfield\n") == ["Jose Alvarez", "Dana Whitfield"]


def test_columns_are_located_by_header_not_position():
    """Exports frequently put email first."""
    raw = b"Email,Last,First\nj@x.com,Alvarez,Jose\n"
    assert names(raw) == ["Jose Alvarez"]


def test_a_single_full_name_column_works():
    assert names(b"Name\nJose Alvarez\nDana Whitfield\n") == ["Jose Alvarez", "Dana Whitfield"]


def test_a_single_column_without_a_header_works():
    assert names(b"Jose Alvarez\nDana Whitfield\n") == ["Jose Alvarez", "Dana Whitfield"]


def test_utf8_bom_is_stripped():
    """Excel's "CSV UTF-8" writes a BOM; without stripping, the first header
    cell fails to match and the header is imported as an agent."""
    assert names("﻿First,Last\nJosé,Alvarez\n".encode("utf-8")) == ["José Alvarez"]


def test_crlf_line_endings():
    assert names(b"First,Last\r\nJose,Alvarez\r\nDana,Whitfield\r\n") == ["Jose Alvarez", "Dana Whitfield"]


def test_a_quoted_field_containing_a_comma():
    assert names(b'Name\n"Alvarez, Jose"\n') == ["Alvarez, Jose"]


def test_semicolon_delimited_european_export():
    assert names(b"First;Last\nJose;Alvarez\n") == ["Jose Alvarez"]


def test_cp1252_bytes_are_decoded():
    assert names("First,Last\nJosé,Alvarez\n".encode("cp1252")) == ["José Alvarez"]


def test_extra_columns_are_ignored():
    assert names(b"First,Last,Email,Team\nJose,Alvarez,j@x.com,Retention\n") == ["Jose Alvarez"]


def test_blank_rows_are_skipped_and_not_counted_invalid():
    out = parse_agent_csv(b"First,Last\nJose,Alvarez\n\n\nDana,Whitfield\n")
    assert out["names"] == ["Jose Alvarez", "Dana Whitfield"]
    assert out["invalid"] == 0


def test_whitespace_is_collapsed_but_case_is_preserved():
    """Never title-case: McDonald, de la Cruz and O'Brien all lose."""
    out = names(b"Name\n  Ronald   McDonald  \nmaria de la Cruz\nSiobhan O'Brien\n")
    assert out == ["Ronald McDonald", "maria de la Cruz", "Siobhan O'Brien"]


def test_rows_with_no_usable_name_are_reported_with_line_numbers():
    out = parse_agent_csv(b"First,Last\nJose,Alvarez\n12345,\n---,\n")
    assert out["names"] == ["Jose Alvarez"]
    assert out["invalid"] == 2
    assert out["invalid_rows"] == [3, 4]


def test_an_over_long_name_is_invalid():
    out = parse_agent_csv(("Name\n" + "A" * (MAX_NAME + 1) + "\n").encode())
    assert out["names"] == [] and out["invalid"] == 1


def test_an_xlsx_gets_a_specific_message():
    out = parse_agent_csv(b"PK\x03\x04rest of a zip container")
    assert out["names"] == [] and "Excel workbook" in out["error"]


def test_a_binary_file_is_refused():
    out = parse_agent_csv(b"\x00\x01\x02binary\x00")
    assert out["names"] == [] and "doesn't look like a CSV" in out["error"]


def test_an_empty_file_is_refused():
    assert "empty" in parse_agent_csv(b"")["error"]


def test_over_the_row_cap_imports_nothing():
    raw = ("Name\n" + "".join(f"Agent {i}\n" for i in range(MAX_ROWS + 1))).encode()
    out = parse_agent_csv(raw)
    assert out["names"] == [] and "limit" in out["error"]


def test_over_the_size_cap_imports_nothing():
    out = parse_agent_csv(b"x" * 1_100_000)
    assert out["names"] == [] and out["error"]


def test_a_header_with_no_name_column_is_refused():
    out = parse_agent_csv(b"Email,Team\nj@x.com,Retention\n")
    assert out["names"] == [] and "name column" in out["error"]


@pytest.mark.parametrize("a,b", [
    ("Jose Alvarez", "jose alvarez"), ("Jose  Alvarez", "Jose Alvarez"),
    (" Jose Alvarez ", "JOSE ALVAREZ"),
])
def test_dedupe_key_is_case_and_whitespace_insensitive(a, b):
    assert dedupe_key(a) == dedupe_key(b)


# ── Route ─────────────────────────────────────────────────────────────────────

def _csv(text: str):
    return (io.BytesIO(text.encode()), "roster.csv")


def _flash_after(actor, path, **kw):
    """Post, then fetch the next page as the same actor and return its body.

    follow_redirects=True cannot be used here: Werkzeug re-issues the followed
    request without our environ override, so the identity is lost and the flash
    is read off the login page instead.
    """
    actor.post(path, **kw)
    return actor.get("/agents/").get_data(as_text=True)


def test_import_adds_agents_and_reports_exactly(tenants, db):
    from models import Agent
    # The fixture org already has "Acme Agent".
    body = _flash_after(tenants.a_admin, "/agents/import", data={"file": _csv(
        "First,Last\nJose,Alvarez\nDana,Whitfield\nAcme,Agent\nJose,Alvarez\n12345,\n")},
        content_type="multipart/form-data")
    assert "Added 2" in body
    assert "skipped 2" in body, "the existing agent and the in-file duplicate must both skip"
    assert "1 invalid row" in body

    db.expire_all()
    all_names = {a.name for a in db.query(Agent).filter_by(org_id=tenants.a["org"]).all()}
    assert all_names == {"Acme Agent", "Jose Alvarez", "Dana Whitfield"}


def test_a_duplicate_differing_only_by_case_is_skipped(tenants, db):
    from models import Agent
    before = db.query(Agent).filter_by(org_id=tenants.a["org"]).count()
    tenants.a_admin.post("/agents/import",
                         data={"file": _csv("Name\n  acme   AGENT  \n")},
                         content_type="multipart/form-data")
    db.expire_all()
    assert db.query(Agent).filter_by(org_id=tenants.a["org"]).count() == before


def test_a_fatal_parse_error_imports_nothing(tenants, db):
    from models import Agent
    before = db.query(Agent).filter_by(org_id=tenants.a["org"]).count()
    body = _flash_after(tenants.a_admin, "/agents/import",
                        data={"file": (io.BytesIO(b"PK\x03\x04zip"), "roster.xlsx")},
                        content_type="multipart/form-data")
    assert "Excel workbook" in body
    db.expire_all()
    assert db.query(Agent).filter_by(org_id=tenants.a["org"]).count() == before


def test_an_mp3_posted_to_the_import_endpoint_does_not_500(tenants):
    r = tenants.a_admin.post("/agents/import",
                             data={"file": (io.BytesIO(b"ID3\x00\x00\x00audio"), "call.mp3")},
                             content_type="multipart/form-data")
    assert r.status_code == 302, "should redirect with a message, not raise"
    body = tenants.a_admin.get("/agents/").get_data(as_text=True)
    assert "CSV" in body


def test_import_is_admin_only(tenants, db):
    from models import Agent
    before = db.query(Agent).filter_by(org_id=tenants.a["org"]).count()
    r = tenants.a_member.post("/agents/import", data={"file": _csv("Name\nNew Person\n")},
                              content_type="multipart/form-data")
    assert r.status_code == 403
    db.expire_all()
    assert db.query(Agent).filter_by(org_id=tenants.a["org"]).count() == before


def test_importing_into_one_org_does_not_touch_another(tenants, db):
    from models import Agent
    before = db.query(Agent).filter_by(org_id=tenants.b["org"]).count()
    tenants.a_admin.post("/agents/import", data={"file": _csv("Name\nJose Alvarez\n")},
                         content_type="multipart/form-data")
    db.expire_all()
    assert db.query(Agent).filter_by(org_id=tenants.b["org"]).count() == before


def test_the_manual_add_now_refuses_a_duplicate_too(tenants, db):
    """The manual and bulk paths must agree on what a duplicate is."""
    from models import Agent
    before = db.query(Agent).filter_by(org_id=tenants.a["org"]).count()
    tenants.a_admin.post("/agents/add", data={"name": "acme agent"})
    db.expire_all()
    assert db.query(Agent).filter_by(org_id=tenants.a["org"]).count() == before
