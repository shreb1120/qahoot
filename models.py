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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="org", cascade="all, delete-orphan"
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
    alv_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pass_fail_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    overrides_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    call: Mapped["Call"] = relationship("Call", back_populates="report")
