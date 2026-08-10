"""History paging, filtering and totals — all of it in SQL.

History used to call `.all()` on an unbounded query with `joinedload(Call.report)`
attached, then slice fifty rows out in Python. Every view of the page read every
call the org had ever uploaded, each with its full JSONB report, to render fifty
of them. It also filtered results with `"FAIL" in status` — a third copy of a
predicate that already existed twice, and one that read the free-text string
where stats.py reads the indexed `verdict` column.

The tests below fix the behaviour that has to survive that change: the counts
are set-wide rather than page-wide, and the filters mean the same thing here as
everywhere else.
"""
import pytest
from sqlalchemy import text

import stats
from conftest import _mk_call
from models import Agent, Call, ComplianceProfile, Organization, Report, User


PAGE_SIZE = 50


@pytest.fixture
def many_calls(tenants, db):
    """Enough calls to span three pages, with a known verdict mix."""
    org = db.get(Organization, tenants.a["org"])
    profile = db.query(ComplianceProfile).filter_by(org_id=org.id).one()
    admin = db.get(User, tenants.a["admin"])
    agent = db.get(Agent, tenants.a["agent"])

    # The fixture already provides one CRITICAL call.
    for i in range(60):
        _mk_call(db, org, profile, admin, agent,
                 verdict="PASS" if i % 2 else "FAIL", internal_id=f"P{i:03d}")
    db.commit()
    return org.id


def _page(actor, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = actor.get(f"/calls/?{qs}")
    assert r.status_code == 200, f"/calls/?{qs} returned {r.status_code}"
    return r.get_data(as_text=True)


# ───────────────────── the totals are not page-scoped ─────────────────────

def test_the_summary_counts_the_whole_set_not_the_page(tenants, db, many_calls):
    """61 calls, 50 on a page. If the counts came from the page they would be
    wrong in a way nobody would notice — they would just be lower."""
    counts = stats.verdict_counts(
        db.query(Call).outerjoin(Report, Report.call_id == Call.id)
        .filter(Call.org_id == many_calls)
    )
    assert counts["total"] == 61
    assert counts["passed"] == 30
    assert counts["failed"] == 30
    assert counts["critical"] == 1

    body = _page(tenants.a_admin)
    assert "61" in body, "the set-wide total should be on the page"


def test_page_two_shows_the_remainder(tenants, db, many_calls):
    body = _page(tenants.a_admin, page=2)
    assert "P0" in body or "1001" in body, "page 2 should render rows"
    body_p1 = _page(tenants.a_admin, page=1)
    assert body_p1 != body, "page 2 must not repeat page 1"


def test_a_page_beyond_the_end_is_clamped(tenants, db, many_calls):
    """A hand-typed ?page=999 should land on the last page, not an empty one."""
    body = _page(tenants.a_admin, page=999)
    assert "P0" in body or "1001" in body


def test_a_junk_page_parameter_does_not_500(tenants, many_calls):
    for bad in ("abc", "-4", "0", "9999999999999999999999"):
        r = tenants.a_admin.get(f"/calls/?page={bad}")
        assert r.status_code == 200, f"page={bad} broke the page"


# ───────────────────── the query is actually bounded ─────────────────────

def test_only_one_page_of_rows_is_fetched(tenants, db, many_calls, monkeypatch):
    """The point of the change. Without a LIMIT this loads 61 Call objects — and
    61 JSONB reports — to display 50."""
    seen = []

    import sqlalchemy.event as event
    from db import get_db

    session = get_db()
    engine = session.get_bind()
    session.close()

    def before(conn, cursor, statement, params, context, executemany):
        if "FROM calls" in statement and "count(" not in statement.lower():
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        tenants.a_admin.get("/calls/")
    finally:
        event.remove(engine, "before_cursor_execute", before)

    row_queries = [s for s in seen if "LIMIT" in s.upper()]
    assert row_queries, "the history row query has no LIMIT — it reads the whole table"


# ───────────────────── filters mean one thing ─────────────────────

@pytest.mark.parametrize("result,expected", [
    ("pass", 30), ("fail", 30), ("critical", 1),
])
def test_the_result_filter_matches_the_shared_predicate(tenants, db, many_calls,
                                                        result, expected):
    """The filter and the dashboard must agree about what a failure is."""
    criterion = stats.verdict_filter(result)
    n = (db.query(Call).join(Report, Report.call_id == Call.id)
         .filter(Call.org_id == many_calls, criterion).count())
    assert n == expected


def test_an_unknown_result_filter_is_ignored_rather_than_empty(tenants, many_calls):
    """A filter value we don't recognise should not silently show zero calls."""
    assert stats.verdict_filter("banana") is None
    body = _page(tenants.a_admin, result="banana")
    assert "61" in body


def test_an_unknown_result_filter_does_not_inflate_the_counts(tenants, many_calls):
    """A stale bookmark or a hand-edited URL takes the no-criterion path. If the
    report join is keyed on `result` being *present* rather than on a criterion
    actually applying, the aggregate references an unjoined `reports` and
    Postgres cross-joins, multiplying every total by the number of reports.

    Asserted on the rendered number rather than on SQLAlchemy's cartesian-product
    warning: that warning is emitted once per code location per process, so any
    earlier test that tripped it would silence this one."""
    body = _page(tenants.a_admin, result="banana")
    assert "61 calls" in body, "the history total is not 61 — the counts query cross-joined"


def test_a_critical_call_is_never_counted_as_an_ordinary_failure(tenants, db):
    """The ILIKE fallback for pre-migration rows is not mutually exclusive:
    "CRITICAL FAIL" matches both patterns. Critical has to win."""
    org = db.get(Organization, tenants.a["org"])
    r = db.query(Report).join(Report.call).filter(Call.org_id == org.id).first()
    r.verdict = None                      # force the free-text fallback
    r.pass_fail_status = "CRITICAL FAIL — Both"
    db.commit()

    counts = stats.verdict_counts(
        db.query(Call).outerjoin(Report, Report.call_id == Call.id)
        .filter(Call.org_id == org.id))
    assert counts["critical"] == 1
    assert counts["failed"] == 0, "a critical failure was reported as an ordinary one"


def test_ungraded_calls_are_excluded_by_a_result_filter(tenants, db):
    """Previous behaviour, via `c.report and …`. An inner join has to preserve it."""
    import uuid
    org = db.get(Organization, tenants.a["org"])
    db.add(Call(id=str(uuid.uuid4()), org_id=org.id,
                uploaded_by_user_id=tenants.a["admin"],
                filename="pending.mp3", status="transcribing"))
    db.commit()

    n = (db.query(Call).join(Report, Report.call_id == Call.id)
         .filter(Call.org_id == org.id, stats.verdict_filter("critical")).count())
    assert n == 1, "the ungraded call leaked into a result-filtered set"


# ───────────────────── isolation still holds ─────────────────────

def test_paging_never_reaches_another_orgs_calls(tenants, db, many_calls):
    body = _page(tenants.b_admin)
    assert "P000" not in body
    counts = stats.verdict_counts(
        db.query(Call).outerjoin(Report, Report.call_id == Call.id)
        .filter(Call.org_id == tenants.b["org"]))
    assert counts["total"] == 1


# ───────────────── the pages cannot disagree ─────────────────

def test_the_dashboard_and_history_report_the_same_numbers(tenants, db, many_calls):
    """The reason the predicate was unified. The dashboard used raw ILIKE on
    `pass_fail_status`; history used Python string matching; stats.py read the
    `verdict` column. Three definitions of "a failure" for one set of calls.

    Made concrete with a report that only the shared predicate reads correctly:
    verdict says pass, the legacy free-text string says FAIL. Whatever the two
    pages decide, they have to decide it together."""
    r = (db.query(Report).join(Report.call)
         .filter(Call.org_id == many_calls, Report.verdict == "pass").first())
    r.pass_fail_status = "FAIL — legacy string"
    db.commit()

    dash = tenants.a_admin.get("/dashboard").get_data(as_text=True)
    hist = _page(tenants.a_admin)

    counts = stats.verdict_counts(
        db.query(Call).join(Report, Report.call_id == Call.id)
        .filter(Call.org_id == many_calls))

    # History's total is every call; the dashboard's buckets are graded calls.
    assert f"{counts['passed']}" in dash
    assert f"{counts['critical']}" in dash
    assert "61 calls" in hist
    assert counts["passed"] + counts["failed"] + counts["critical"] == 61
