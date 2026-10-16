"""ORM mappings. The authoritative DDL (constraints, RLS, grants, triggers) is in migrations/."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, DATERANGE, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email_hash: Mapped[str] = mapped_column(Text)
    ip_hash: Mapped[str] = mapped_column(Text)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "memberships"

    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    products_rows: Mapped[int | None] = mapped_column(Integer)
    customers_rows: Mapped[int | None] = mapped_column(Integer)
    orders_rows: Mapped[int | None] = mapped_column(Integer)
    returns_rows: Mapped[int | None] = mapped_column(Integer)
    date_min: Mapped[date | None] = mapped_column(Date)
    date_max: Mapped[date | None] = mapped_column(Date)
    checksum_sha256: Mapped[str | None] = mapped_column(Text)
    source_timezone: Mapped[str] = mapped_column(Text)
    business_timezone: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    issue_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetFile(Base):
    __tablename__ = "dataset_files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    raw: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    headers: Mapped[list[str]] = mapped_column(JSONB)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    mapping_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    mapping_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_accepted: Mapped[int | None] = mapped_column(Integer)
    rows_rejected: Mapped[int | None] = mapped_column(Integer)
    rows_duplicate: Mapped[int | None] = mapped_column(Integer)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"))
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    file_kind: Mapped[str] = mapped_column(Text)
    row_number: Mapped[int | None] = mapped_column(Integer)
    column_name: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    business_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID | None] = mapped_column()
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger, default=100)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    ip_prefix: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Canonical dataset records (insert-only; keyed by dataset and natural key) ----


class Product(Base):
    __tablename__ = "products"

    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    list_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    source_row: Mapped[int] = mapped_column(Integer)


class Customer(Base):
    __tablename__ = "customers"

    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    signup_date: Mapped[date | None] = mapped_column(Date)
    region: Mapped[str | None] = mapped_column(Text)
    acquisition_channel: Mapped[str | None] = mapped_column(Text)
    source_row: Mapped[int] = mapped_column(Integer)


class OrderLine(Base):
    __tablename__ = "order_lines"

    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    order_line_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[str] = mapped_column(Text)
    order_date: Mapped[date] = mapped_column(Date)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    channel: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    source_row: Mapped[int] = mapped_column(Integer)


class Return(Base):
    __tablename__ = "returns"

    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    return_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_line_id: Mapped[str] = mapped_column(Text)
    return_date: Mapped[date] = mapped_column(Date)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quantity_returned: Mapped[int] = mapped_column(Integer)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    reason_code: Mapped[str] = mapped_column(Text)
    reason_text: Mapped[str | None] = mapped_column(Text)
    source_row: Mapped[int] = mapped_column(Integer)


# ---- Analytics (AMD-10) ----


class AnalysisRevision(Base):
    __tablename__ = "analysis_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column()
    config_version: Mapped[int] = mapped_column(Integer)
    code_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    current_window: Mapped[Any] = mapped_column(DATERANGE)
    baseline_window: Mapped[Any] = mapped_column(DATERANGE)
    restrictions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics_hash: Mapped[str | None] = mapped_column(Text)
    segments_hash: Mapped[str | None] = mapped_column(Text)
    incidents_hash: Mapped[str | None] = mapped_column(Text)
    revision_hash: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerSegment(Base):
    __tablename__ = "customer_segments"

    business_id: Mapped[uuid.UUID] = mapped_column()
    revision_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    segment: Mapped[str] = mapped_column(Text)
    r_score: Mapped[int] = mapped_column(SmallInteger)
    f_score: Mapped[int] = mapped_column(SmallInteger)
    m_score: Mapped[int] = mapped_column(SmallInteger)
    first_order_date: Mapped[date] = mapped_column(Date)
    last_order_date: Mapped[date] = mapped_column(Date)
    recency_days: Mapped[int] = mapped_column(Integer)
    lifetime_orders: Mapped[int] = mapped_column(Integer)
    frequency_365: Mapped[int] = mapped_column(Integer)
    monetary_365: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    median_gap_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    revision_id: Mapped[uuid.UUID] = mapped_column()
    key: Mapped[str] = mapped_column(Text)
    metric: Mapped[str] = mapped_column(Text)
    dimension: Mapped[str] = mapped_column(Text)
    dimension_value: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    impact: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB)
    affected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    revision_id: Mapped[uuid.UUID] = mapped_column()
    incident_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_hash: Mapped[str] = mapped_column(Text)
    params_hash: Mapped[str | None] = mapped_column(Text)
    query_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- Investigations (AMD-12) ----


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    dataset_id: Mapped[uuid.UUID] = mapped_column()
    revision_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(Text)
    llm_mode: Mapped[str] = mapped_column(Text)
    llm_disabled_reason: Mapped[str | None] = mapped_column(Text)
    graph_version: Mapped[str] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvestigationEvent(Base):
    __tablename__ = "investigation_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column()
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    actor_kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column()
    task: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(SmallInteger)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer)
    validation: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column()
    incident_id: Mapped[uuid.UUID] = mapped_column()
    template: Mapped[str] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text)
    rationale: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HypothesisEvaluation(Base):
    __tablename__ = "hypothesis_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    hypothesis_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    data_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    human_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InvestigationEvidence(Base):
    __tablename__ = "investigation_evidence"

    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column()
    requested_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Clarification(Base):
    __tablename__ = "clarifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column()
    incident_id: Mapped[uuid.UUID] = mapped_column()
    incident_key: Mapped[str] = mapped_column(Text)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column()
    criterion_key: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    question_spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    business_id: Mapped[uuid.UUID] = mapped_column()
    investigation_id: Mapped[uuid.UUID] = mapped_column()
    round: Mapped[int] = mapped_column(SmallInteger)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rendered: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
