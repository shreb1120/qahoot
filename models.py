"""
SQLAlchemy models for the qaboom multi-tenant schema.

All tenant-owned tables carry an org_id FK to organizations. Every query
that touches these tables MUST filter by org_id — enforcement happens in
application code (see blueprints). The DB-level FK + index make accidental
cross-tenant data leaks loudly wrong even if application code fails.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # How long uploaded audio is kept. NULL means keep it indefinitely, which
    # is the shipped default — the retention job exists and runs, but deletes
    # nothing until someone sets a window. Deleting evidence a compliance
    # report cites is not recoverable, so this is opt-in per org.
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="org", cascade="all, delete-orphan"
    )
    subscription: Mapped["Subscription | None"] = relationship(
        "Subscription", back_populates="org", uselist=False,
        cascade="all, delete-orphan",
    )
    compliance_profiles: Mapped[list["ComplianceProfile"]] = relationship(
        "ComplianceProfile", back_populates="org", cascade="all, delete-orphan"
    )
    calls: Mapped[list["Call"]] = relationship(
        "Call", back_populates="org", cascade="all, delete-orphan"
    )
    agents: Mapped[list["Agent"]] = relationship(
        "Agent", back_populates="org", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_org_id", "org_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    org: Mapped["Organization"] = relationship("Organization", back_populates="agents")
    calls: Mapped[list["Call"]] = relationship("Call", back_populates="agent")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),
        Index("ix_users_org_id", "org_id"),
    )

    # id = Clerk user ID (e.g. "user_2abc123..."); not a UUID we generate.
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    org: Mapped["Organization | None"] = relationship(
        "Organization", back_populates="users"
    )


# ---------------------------------------------------------------------------
# Org invites (link-based; no email sending required in v1)
# ---------------------------------------------------------------------------

class OrgInvite(Base):
    __tablename__ = "org_invites"
    __table_args__ = (
        Index("ix_org_invites_org_id", "org_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional: admin can pre-fill who the invite is for (display only, not enforced).
    invited_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member"
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    org: Mapped["Organization"] = relationship("Organization")


# ---------------------------------------------------------------------------
# Compliance profiles (per-org, configurable checklist)
# ---------------------------------------------------------------------------

class ComplianceProfile(Base):
    __tablename__ = "compliance_profiles"
    __table_args__ = (
        Index("ix_compliance_profiles_org_id", "org_id"),
        # An org has exactly one checklist in force. profile_bp maintains this
        # by deactivating the previous profile before inserting the new one;
        # the partial index is what makes two concurrent switches impossible
        # rather than merely unlikely. Declared here as well as in the
        # migration so create_all builds it and the tests exercise it.
        Index("ix_profiles_one_active", "org_id",
              unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Full checklist stored as JSONB. Shape defined in templates_seed.py.
    script_sections_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    org: Mapped["Organization"] = relationship(
        "Organization", back_populates="compliance_profiles"
    )
    calls: Mapped[list["Call"]] = relationship(
        "Call", back_populates="compliance_profile"
    )


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','transcribing','analyzing','complete','error')",
            name="ck_calls_status",
        ),
        Index("ix_calls_org_id", "org_id"),
        Index("ix_calls_org_status", "org_id", "status"),
        Index("ix_calls_org_agent", "org_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    compliance_profile_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("compliance_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    internal_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    call_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    audio_file_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    org: Mapped["Organization"] = relationship(
        "Organization", back_populates="calls"
    )
    compliance_profile: Mapped["ComplianceProfile | None"] = relationship(
        "ComplianceProfile", back_populates="calls"
    )
    uploader: Mapped["User | None"] = relationship("User", foreign_keys=[uploaded_by_user_id])
    agent: Mapped["Agent | None"] = relationship("Agent", back_populates="calls")
    transcript: Mapped["Transcript | None"] = relationship(
        "Transcript",
        back_populates="call",
        uselist=False,
        cascade="all, delete-orphan",
    )
    report: Mapped["Report | None"] = relationship(
        "Report",
        back_populates="call",
        uselist=False,
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------

class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    raw_transcript_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    call: Mapped["Call"] = relationship("Call", back_populates="transcript")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_verdict", "verdict"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Human-readable determination, e.g. "FAIL — Approval Script". Display only.
    pass_fail_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The same verdict, machine-readable: "pass" | "fail" | "critical".
    # Every pass-rate query used to scan pass_fail_status with ILIKE '%…%',
    # which can never use an index, and free text drifts — production still
    # holds a pre-normalizer "FAIL — Both" that no current code path emits.
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    overrides_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="report")


# ---------------------------------------------------------------------------
# Billing: usage ledger, period counters, subscriptions
# ---------------------------------------------------------------------------
#
# Three tables with three different jobs, and it matters that they stay
# separate:
#
#   usage_events    an append-only ledger of what was actually consumed. This
#                   is the evidence behind an invoice, so nothing ever updates
#                   or deletes a row, and the foreign keys deliberately refuse
#                   to cascade — see the note on org_id below.
#   usage_periods   a running counter, so "how much have they used" is a
#                   primary-key read rather than a SUM over the ledger.
#   subscriptions   a local cache of Stripe's state. Stripe is the source of
#                   truth; this exists so every request doesn't call their API.


class UsageEvent(Base):
    """One row per billable resource consumed — not one per call.

    Transcription and analysis fail independently and cost different vendors
    different money. If grading fails after transcription succeeded, the
    transcription row is already committed, the org is correctly billed for the
    minutes AssemblyAI really consumed, and no analysis row exists. A single
    per-call row could not express that.
    """
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_org_period", "org_id", "period_start", "resource_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # RESTRICT, not CASCADE. Organization.calls already cascades delete-orphan;
    # letting that reach the ledger would erase an invoice's own evidence.
    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # SET NULL for the same reason: a deleted call must not take its billing
    # record with it. Retention deletion removes the audio file, never this row.
    call_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)  # transcription|analysis
    # Seconds of audio. Stored in seconds and rounded to minutes exactly once,
    # at period aggregation — per-call rounding would bill 3,000 half-minute
    # calls as 3,000 minutes instead of 1,500.
    billable_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What the vendor charged us, in millionths of a dollar. Margin analysis,
    # never shown to the customer.
    vendor_cost_micros: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Denormalized at write so a call straddling a period boundary is
    # attributed to the period the usage occurred in, deterministically.
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The exactly-once guarantee. pipeline._running is in-process only and
    # survives nothing; this constraint does.
    idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    org: Mapped["Organization"] = relationship("Organization")


class UsagePeriod(Base):
    """The counter the app actually reads. Never SUM() the ledger for display."""
    __tablename__ = "usage_periods"

    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Snapshot of the plan's allowance when the period opened, so changing a
    # plan mid-period cannot retroactively rewrite what was included.
    included_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billable_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warn_80_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    warn_100_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    org: Mapped["Organization"] = relationship("Organization")


class Subscription(Base):
    """Local cache of the org's Stripe state. Stripe remains authoritative."""
    __tablename__ = "subscriptions"

    org_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan_code: Mapped[str] = mapped_column(String(40), nullable=False, default="trial")
    # Mirrors Stripe's vocabulary — trialing, active, past_due, canceled, unpaid.
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="trialing")
    included_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overage_micros_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    org: Mapped["Organization"] = relationship("Organization", back_populates="subscription")


class StripeEvent(Base):
    """Webhook replay guard.

    Stripe retries deliveries and will send the same event twice. The primary
    key is Stripe's own event id: insert first, and a conflict means we have
    already processed it.
    """
    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RateLimitCounter(Base):
    """Fixed-window abuse counters.

    Deliberately *not* a billing feature. Soft overage means the quota never
    blocks an upload, which leaves no ceiling on a compromised account; these
    limits are that ceiling, and sit far above any real customer's burst.
    Postgres-backed because there is no redis, and incremented with a single
    atomic INSERT … ON CONFLICT DO UPDATE so concurrent uploads cannot race.
    """
    __tablename__ = "rate_limit_counters"

    scope_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
