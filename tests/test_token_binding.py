"""A valid signature is not the same as a token meant for us.

Clerk session tokens were verified for signature and expiry only — no issuer,
no `azp`. `azp` is the origin that asked Clerk for the token, and it is the
load-bearing one here: a Clerk *development* instance answers any origin and
our publishable key is public in the page source, so another site can have
Clerk mint a genuine, correctly-signed session token for a visiting user of
ours. Replayed as `Authorization: Bearer`, it was accepted — full account
takeover with a signature that verifies perfectly.

These tests mint real RS256 tokens with a real key and run them through the
actual verifier, so they exercise the code the attack would.
"""
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt

import auth

ISSUER = "https://smiling-gnat-71.clerk.accounts.dev"
OURS = "https://qaboom.io"
ATTACKER = "https://evil.example.com"


@pytest.fixture(scope="module")
def key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k, k.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture
def verify(app, key, monkeypatch):
    """The real verifier, with only the JWKS lookup replaced."""
    priv, pub = key

    class FakeKey:
        def __init__(self, k): self.key = k

    monkeypatch.setattr(auth, "_get_jwks_client",
                        lambda: type("C", (), {
                            "get_signing_key_from_jwt": staticmethod(lambda t: FakeKey(pub))
                        })())

    app.config["CLERK_ISSUER"] = ISSUER
    app.config["CLERK_AUTHORIZED_PARTIES"] = (OURS,)

    def _verify(**claims):
        now = int(time.time())
        payload = {"sub": "user_1", "iss": ISSUER, "azp": OURS,
                   "iat": now, "nbf": now - 1, "exp": now + 60}
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        token = pyjwt.encode(payload, priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()), algorithm="RS256")
        with app.test_request_context():
            return auth._verify_session_token(token)
    return _verify


# ─────────────────────── the attack ───────────────────────

def test_a_token_minted_for_another_origin_is_refused(verify):
    """The finding. This token is genuine and its signature verifies — it was
    simply obtained by somebody else's website."""
    assert verify(azp=ATTACKER) is None, \
        "a validly-signed token issued to another origin was accepted"


def test_our_own_token_still_works(verify):
    """The fix must not lock out every real user, which is the obvious way to
    get this wrong."""
    claims = verify()
    assert claims is not None and claims["sub"] == "user_1"


def test_a_token_from_another_clerk_instance_is_refused(verify):
    assert verify(iss="https://someone-else.clerk.accounts.dev") is None


# ─────────────────────── the deliberate softness ───────────────────────

def test_a_token_without_azp_is_still_accepted(verify):
    """Enforced-when-present, not required.

    Clerk puts azp in every token this instance issues, so this rule blocks the
    attack completely — the attacker's token *has* an azp, just the wrong one.
    Hard-requiring it would add nothing against that attack while risking
    locking out every user if Clerk changed its claim set."""
    assert verify(azp=None) is not None


def test_no_audience_is_required(verify):
    """Clerk does not put `aud` in these tokens; requiring one would reject
    every real request. Verified against a live token before deciding."""
    assert verify(aud=None) is not None


# ─────────────────────── the ordinary checks still hold ───────────────────────

def test_an_expired_token_is_refused(verify):
    now = int(time.time())
    assert verify(exp=now - 10, nbf=now - 100, iat=now - 100) is None


def test_a_token_signed_by_the_wrong_key_is_refused(app, key, monkeypatch):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _, pub = key

    class FakeKey:
        def __init__(self, k): self.key = k

    monkeypatch.setattr(auth, "_get_jwks_client",
                        lambda: type("C", (), {
                            "get_signing_key_from_jwt": staticmethod(lambda t: FakeKey(pub))
                        })())
    app.config["CLERK_ISSUER"] = ISSUER
    app.config["CLERK_AUTHORIZED_PARTIES"] = (OURS,)

    now = int(time.time())
    token = pyjwt.encode({"sub": "u", "iss": ISSUER, "azp": OURS,
                          "nbf": now - 1, "exp": now + 60},
                         other.private_bytes(
                             serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()), algorithm="RS256")
    with app.test_request_context():
        assert auth._verify_session_token(token) is None


def test_the_configured_parties_include_the_live_domain():
    """A deploy that dropped the real domain from the allowlist would lock
    everyone out, and the symptom (silent 401s) would be baffling."""
    from config import Config
    assert "https://qaboom.io" in Config.CLERK_AUTHORIZED_PARTIES
