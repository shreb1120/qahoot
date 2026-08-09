"""Test harness.

Runs against a dedicated database, never the app's own. Nothing here talks to
Clerk: the tests replace the *identity* a request arrives with, then let the
real login_required / org_required / admin_required decorators and the real
route queries run untouched. That boundary is deliberate — these tests prove
the application's authorization and scoping, not Clerk's token verification.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB_URL = os.environ.get(
    "QABOOM_TEST_DATABASE_URL",
    "postgresql://qahoot:qahoot_dev@localhost/qahoot_test",
)

from config import Config  # noqa: E402


class TestConfig(Config):
    DATABASE_URL = TEST_DB_URL
    TESTING = True
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = "/tmp/qaboom-test-uploads"


def _guard_not_production():
    """A wrong URL here would run destructive schema operations on real data."""
    from config import Config as RealConfig
    real = os.environ.get("DATABASE_URL") or RealConfig.DATABASE_URL
    if TEST_DB_URL == real:
        raise RuntimeError(
            f"Refusing to run: test URL equals the app's DATABASE_URL ({real}). "
            "Create a separate database and set QABOOM_TEST_DATABASE_URL."
        )
    if not TEST_DB_URL.rstrip("/").endswith(("_test", "_testing")):
        raise RuntimeError(
            f"Refusing to run: {TEST_DB_URL} does not look like a test database."
        )


@pytest.fixture(scope="session")
def app():
    _guard_not_production()
    os.makedirs(TestConfig.UPLOAD_FOLDER, exist_ok=True)

    from app import create_app
    from db import Base, get_db
    import models  # noqa: F401  — registers every table on Base.metadata

    application = create_app(TestConfig)

    session = get_db()
    engine = session.get_bind()
    session.close()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # Identity injection. Registered after create_app, so it runs after the real
    # load_user — the decorators under test still see whatever this sets.
    @application.before_request
    def _inject_identity():
        from flask import g, request
        ident = request.environ.get("qaboom.identity")
        if ident is None:
            return
        from models import Organization, User
        g.user = g.db.query(User).filter_by(id=ident["user_id"]).first()
        g.org = (
            g.db.query(Organization).filter_by(id=ident["org_id"]).first()
            if ident.get("org_id") else None
        )

    yield application

    Base.metadata.drop_all(engine)


@pytest.fixture
def db(app):
    from db import get_db
    s = get_db()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Each test starts from an empty schema."""
    from db import Base, get_db
    s = get_db()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()
    finally:
        s.close()
    yield


class Actor:
    """A signed-in identity. Requests made through it carry that identity."""

    def __init__(self, client, user_id, org_id):
        self.client = client
        self.user_id = user_id
        self.org_id = org_id

    def _env(self):
        return {"qaboom.identity": {"user_id": self.user_id, "org_id": self.org_id}}

    def get(self, path, **kw):
        return self.client.get(path, environ_overrides=self._env(), **kw)

    def post(self, path, **kw):
        return self.client.post(path, environ_overrides=self._env(), **kw)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def anon(client):
    """Nobody signed in — no identity is injected at all."""
    return client


def _mk_org(s, name):
    from models import Organization
    o = Organization(id=str(uuid.uuid4()), name=name)
    s.add(o)
    return o


def _mk_user(s, org, email, role):
    from models import User
    u = User(id=f"user_{uuid.uuid4().hex[:12]}", org_id=org.id, email=email, role=role)
    s.add(u)
    return u


def _mk_profile(s, org):
    from models import ComplianceProfile
    p = ComplianceProfile(
        id=str(uuid.uuid4()), org_id=org.id, name=f"{org.name} checklist",
        is_active=True,
        script_sections_json={
            "sections": [{
                "name": "Approval Script", "key": "approval_script",
                "items": [
                    {"name": "Disclose fees", "required": True, "notes": "state the percentage"},
                    {"name": "State cancellation policy", "required": True, "notes": ""},
                ],
            }],
            "auto_fail_phrases": [{"phrase": "guaranteed settlement", "description": "prohibited"}],
        },
    )
    s.add(p)
    return p


def _mk_agent(s, org, name):
    from models import Agent
    a = Agent(id=str(uuid.uuid4()), org_id=org.id, name=name)
    s.add(a)
    return a


def _mk_call(s, org, profile, user, agent, *, status="complete", verdict="FAIL", alv="1001"):
    from models import Call, Report, Transcript
    c = Call(
        id=str(uuid.uuid4()), org_id=org.id, compliance_profile_id=profile.id,
        uploaded_by_user_id=user.id, agent_id=agent.id, alv_id=alv,
        call_date=date(2026, 8, 1), client_phone="8005551234",
        filename=f"{org.name.lower()}-call.mp3", status=status, duration=300,
        audio_file_url=os.path.join(TestConfig.UPLOAD_FOLDER, f"{org.name}.mp3"),
    )
    s.add(c)
    s.flush()
    if status == "complete":
        s.add(Transcript(id=str(uuid.uuid4()), call_id=c.id, raw_transcript_json={
            "utterances": [
                {"speaker": "A", "start": 0, "end": 5000, "text": f"{org.name} secret content"},
                {"speaker": "B", "start": 5600, "end": 9000, "text": "understood"},
            ],
            "text": f"{org.name} secret content",
        }))
        s.add(Report(id=str(uuid.uuid4()), call_id=c.id, pass_fail_status=verdict,
                     overrides_json=None, report_json={
            "final_determination": verdict,
            "summary": f"{org.name} confidential summary",
            "sections": [{
                "name": "Approval Script", "key": "approval_script",
                "covered_count": 1, "total_count": 2,
                "items": [
                    {"name": "Disclose fees", "status": "covered", "timestamp": "0:32",
                     "evidence": f"{org.name} evidence quote"},
                    {"name": "State cancellation policy", "status": "missing",
                     "timestamp": "—", "evidence": ""},
                ],
            }],
            "auto_fail_phrases": {"detected": verdict.startswith("CRITICAL"), "phrases": (
                [{"phrase": "guaranteed settlement", "timestamp": "2:41", "speaker": "A",
                  "quote": "you'll get a guaranteed settlement", "violation": "prohibited"}]
                if verdict.startswith("CRITICAL") else []
            )},
        }))
    return c


class Tenant:
    def __init__(self, org, admin, member, profile, agent, call):
        self.org, self.admin, self.member = org, admin, member
        self.profile, self.agent, self.call = profile, agent, call


def _build_tenant(s, name, verdict="FAIL"):
    org = _mk_org(s, name)
    s.flush()
    admin = _mk_user(s, org, f"admin@{name.lower()}.test", "admin")
    member = _mk_user(s, org, f"member@{name.lower()}.test", "member")
    profile = _mk_profile(s, org)
    agent = _mk_agent(s, org, f"{name} Agent")
    s.flush()
    call = _mk_call(s, org, profile, admin, agent, verdict=verdict)
    return Tenant(org, admin, member, profile, agent, call)


@pytest.fixture
def tenants(app, client):
    """Two fully-populated, unrelated organizations."""
    from db import get_db
    s = get_db()
    try:
        a = _build_tenant(s, "Acme", verdict="CRITICAL FAIL")
        b = _build_tenant(s, "Borden", verdict="PASS")
        s.commit()
        # Write real audio files so the audio route is exercised, not short-circuited.
        for t in (a, b):
            with open(t.call.audio_file_url, "wb") as fh:
                fh.write(b"ID3" + t.org.name.encode() + b"\x00" * 64)
        data = {
            "a": {"org": a.org.id, "admin": a.admin.id, "member": a.member.id,
                  "agent": a.agent.id, "call": a.call.id, "name": a.org.name},
            "b": {"org": b.org.id, "admin": b.admin.id, "member": b.member.id,
                  "agent": b.agent.id, "call": b.call.id, "name": b.org.name},
        }
    finally:
        s.close()

    class T:
        pass
    t = T()
    t.raw = data
    t.a_admin = Actor(client, data["a"]["admin"], data["a"]["org"])
    t.a_member = Actor(client, data["a"]["member"], data["a"]["org"])
    t.b_admin = Actor(client, data["b"]["admin"], data["b"]["org"])
    t.b_member = Actor(client, data["b"]["member"], data["b"]["org"])
    t.a = data["a"]
    t.b = data["b"]
    return t


# ── Live API tests ────────────────────────────────────────────────────────────
# Off by default: they cost money and need network. Run with:
#     pytest --live -m live

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run tests that make real Anthropic API calls")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: makes a real Anthropic API call")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (makes a real Anthropic API call)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
