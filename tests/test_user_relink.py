"""Signing up again with an address you have used before.

Clerk issues a brand new `sub` whenever an account is deleted and remade, or
when someone signs up again with an address they used before. `users.email` is
UNIQUE here, so the insert in _sync_user raised IntegrityError — from inside a
before_request hook, which meant every authenticated request died and the person
was bounced to the landing page. Permanently, with no route out from the UI.

It locked a real user out during a live test, which is how it was found.
"""
import uuid
from datetime import datetime, timezone

import pytest

import auth
from models import Call, Organization, Report, User


def _new_clerk_id():
    return "user_" + uuid.uuid4().hex[:24]


@pytest.fixture
def verified(monkeypatch):
    calls = []

    def _fake(clerk_user_id, email):
        calls.append((clerk_user_id, email))
        return _fake.result

    _fake.result = True
    _fake.calls = calls
    monkeypatch.setattr(auth, "_email_is_verified", _fake)
    return _fake



def test_signing_up_again_with_the_same_email_does_not_break_login(tenants, db, verified):
    """The bug, end to end. This used to raise IntegrityError."""
    existing = db.get(User, tenants.a["admin"])
    email = existing.email
    new_id = _new_clerk_id()

    user = auth._sync_user(new_id, email, db)

    assert user.id == new_id
    assert user.email == email


def test_the_org_and_role_come_with_them(tenants, db, verified):
    """Otherwise the person signs in successfully and finds an empty account,
    which is arguably worse than the error."""
    existing = db.get(User, tenants.a["admin"])
    email, org_id, role = existing.email, existing.org_id, existing.role
    assert role == "admin", "fixture should be an admin for this to mean anything"

    user = auth._sync_user(_new_clerk_id(), email, db)

    assert user.org_id == org_id
    assert user.role == "admin", "an admin came back as a member — they would lose access"


def test_their_history_follows_them(tenants, db, verified):
    """Calls they uploaded and reviews they signed off must not be orphaned."""
    old_id = tenants.a["admin"]
    call_id = tenants.a["call"]
    db.get(Call, call_id).uploaded_by_user_id = old_id
    db.get(Call, call_id).report.reviewed_by_user_id = old_id
    db.commit()

    new_id = _new_clerk_id()
    auth._sync_user(new_id, db.get(User, old_id).email, db)
    db.expire_all()

    assert db.get(Call, call_id).uploaded_by_user_id == new_id
    assert db.get(Call, call_id).report.reviewed_by_user_id == new_id


def test_the_old_identity_is_gone(tenants, db, verified):
    """A leftover row would keep the unique index occupied and re-break the next
    sign-in with that address."""
    old_id = tenants.a["admin"]
    email = db.get(User, old_id).email
    auth._sync_user(_new_clerk_id(), email, db)
    db.expire_all()

    assert db.get(User, old_id) is None
    assert db.query(User).filter_by(email=email).count() == 1


def test_a_genuinely_new_person_still_gets_a_fresh_record(tenants, db):
    new_id = _new_clerk_id()
    user = auth._sync_user(new_id, f"{new_id}@example.com", db)
    assert user.id == new_id
    assert user.org_id is None, "a new user must land on org setup, not in someone's org"
    assert user.role == "member"


def test_a_returning_user_is_untouched(tenants, db):
    """The ordinary path must not go anywhere near the re-link logic."""
    existing = db.get(User, tenants.a["admin"])
    same = auth._sync_user(existing.id, existing.email, db)
    assert same.id == existing.id
    assert same.role == existing.role


def test_no_email_means_no_adoption(tenants, db):
    """An absent email claim must never match an existing account — matching on
    empty string would hand out the first emailless row to anyone."""
    first = auth._sync_user(_new_clerk_id(), "", db)
    second = auth._sync_user(_new_clerk_id(), "", db)

    assert first.id != second.id, "an emailless token adopted someone else's record"
    # users.email is NOT NULL UNIQUE, so two emailless users cannot both be ""
    # — they would collide and 500 exactly like the bug this file is about.
    assert first.email != second.email
    assert first.email.endswith(".invalid"), "placeholder must be unroutable"


# ═══════════════ re-linking must not become account takeover ═══════════════
#
# Adopting an existing account because the email matches is a takeover if that
# address was never proven: signing up as someone@example.com would inherit
# their organization and their admin role. This instance does demand an emailed
# code, but the session token carries no `email_verified` claim, so the app
# cannot see that — and an instance setting can change without this code being
# touched. Flagged by a security review of the original fix.

def test_an_unverified_address_never_adopts_an_existing_account(tenants, db, verified):
    """The takeover. They must land on org setup, not in someone else's org."""
    verified.result = False
    victim = db.get(User, tenants.a["admin"])
    victim_org, victim_role = victim.org_id, victim.role

    attacker = auth._sync_user(_new_clerk_id(), victim.email, db)

    assert attacker.org_id is None, "inherited the victim's organization"
    assert attacker.role != "admin", "inherited the victim's admin role"
    db.expire_all()
    assert db.get(User, tenants.a["admin"]).org_id == victim_org
    assert db.get(User, tenants.a["admin"]).role == victim_role


def test_an_unknown_verification_status_also_refuses(tenants, db, verified):
    """Clerk unreachable, or no secret key configured. Not-a-clear-yes is a no."""
    verified.result = None
    victim = db.get(User, tenants.a["admin"])
    attacker = auth._sync_user(_new_clerk_id(), victim.email, db)
    assert attacker.org_id is None


def test_refusing_still_does_not_500(tenants, db, verified):
    """The bug this whole path exists to fix must stay fixed: whatever we
    decide, the unique index must not be violated."""
    verified.result = False
    email = db.get(User, tenants.a["admin"]).email
    first = auth._sync_user(_new_clerk_id(), email, db)
    second = auth._sync_user(_new_clerk_id(), email, db)
    assert first.id != second.id and first.email != second.email


def test_a_verified_address_still_re_links(tenants, db, verified):
    """The legitimate case — a real person who remade their Clerk account —
    must keep working, or the security fix has just recreated the lockout."""
    verified.result = True
    victim = db.get(User, tenants.a["admin"])
    email, org_id = victim.email, victim.org_id

    new_id = _new_clerk_id()
    user = auth._sync_user(new_id, email, db)

    assert user.id == new_id
    assert user.org_id == org_id
    assert user.role == "admin"


def test_verification_is_asked_about_the_right_person(tenants, db, verified):
    """Asking about the *existing* user would confirm an address the attacker
    does not control and defeat the check entirely."""
    victim_email = db.get(User, tenants.a["admin"]).email
    new_id = _new_clerk_id()
    auth._sync_user(new_id, victim_email, db)

    asked_id, asked_email = verified.calls[-1]
    assert asked_id == new_id, "confirmed the wrong person's address"
    assert asked_email == victim_email
