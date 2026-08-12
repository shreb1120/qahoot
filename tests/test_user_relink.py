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


def test_signing_up_again_with_the_same_email_does_not_break_login(tenants, db):
    """The bug, end to end. This used to raise IntegrityError."""
    existing = db.get(User, tenants.a["admin"])
    email = existing.email
    new_id = _new_clerk_id()

    user = auth._sync_user(new_id, email, db)

    assert user.id == new_id
    assert user.email == email


def test_the_org_and_role_come_with_them(tenants, db):
    """Otherwise the person signs in successfully and finds an empty account,
    which is arguably worse than the error."""
    existing = db.get(User, tenants.a["admin"])
    email, org_id, role = existing.email, existing.org_id, existing.role
    assert role == "admin", "fixture should be an admin for this to mean anything"

    user = auth._sync_user(_new_clerk_id(), email, db)

    assert user.org_id == org_id
    assert user.role == "admin", "an admin came back as a member — they would lose access"


def test_their_history_follows_them(tenants, db):
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


def test_the_old_identity_is_gone(tenants, db):
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
