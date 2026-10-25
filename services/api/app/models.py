from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class Actor(Base):
    __tablename__ = "actors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(64))


class SourceArtifact(Base):
    __tablename__ = "source_artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    kind: Mapped[str] = mapped_column(String(64))
    source_system: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    versions: Mapped[list["ArtifactVersion"]] = relationship(back_populates="artifact")


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("source_artifacts.id"))
    version: Mapped[int] = mapped_column()
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    document_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    business_valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    business_valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    availability_status: Mapped[str] = mapped_column(String(32))
    availability_evidence: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON)
    artifact: Mapped[SourceArtifact] = relationship(back_populates="versions")
    grants: Mapped[list["AccessGrant"]] = relationship(back_populates="version")
    facts: Mapped[list["Fact"]] = relationship(back_populates="version")


class AccessGrant(Base):
    __tablename__ = "access_grants"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"))
    role: Mapped[str] = mapped_column(String(64))
    grant_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    grant_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permission: Mapped[str] = mapped_column(String(32), default="read")
    version: Mapped[ArtifactVersion] = relationship(back_populates="grants")


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    artifact_version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"))
    fact_type: Mapped[str] = mapped_column(String(64))
    entity_ref: Mapped[str] = mapped_column(String(80), default="")
    amount_decimal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    value_text: Mapped[str] = mapped_column(Text, default="")
    source_locator: Mapped[str] = mapped_column(String(200), default="")
    extraction_status: Mapped[str] = mapped_column(String(32), default="seeded")
    version: Mapped[ArtifactVersion] = relationship(back_populates="facts")


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    from_node_id: Mapped[str] = mapped_column(String(80))
    to_node_id: Mapped[str] = mapped_column(String(80))
    edge_type: Mapped[str] = mapped_column(String(64))
    supporting_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="high")
    is_inferred: Mapped[bool] = mapped_column(default=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    case_type: Mapped[str] = mapped_column(String(64))
    decision_artifact_id: Mapped[str] = mapped_column(String(64))
    decision_version_id: Mapped[str] = mapped_column(String(64))
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    vendor_name: Mapped[str] = mapped_column(String(200))
    entity_ref: Mapped[str] = mapped_column(String(80))
    recorded_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    probe_version_ids: Mapped[list] = mapped_column(JSON)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("case_id", "actor_id", "as_of", "mode", name="uq_snapshot_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(32))
    allowed_manifest_json: Mapped[list] = mapped_column(JSON)
    excluded_counts_json: Mapped[dict] = mapped_column(JSON)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    agent_version: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finding_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32))


class ToolEvent(Base):
    __tablename__ = "tool_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"))
    tool_name: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[dict] = mapped_column(JSON)
    result_ids_json: Mapped[list] = mapped_column(JSON)
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id"))
    action_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("actors.id"))
    decision: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_event_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IngestDocument(Base):
    __tablename__ = "ingest_documents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingest_jobs.id"))
    filename: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(120), default="text/plain")
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")


class IngestRecord(Base):
    __tablename__ = "ingest_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingest_jobs.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("ingest_documents.id"))
    external_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(32))
    vendor: Mapped[str] = mapped_column(String(200), default="")
    entity_ref: Mapped[str] = mapped_column(String(80), default="")
    references_json: Mapped[list] = mapped_column(JSON)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)
    formula: Mapped[str] = mapped_column(Text, default="")
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    version_id: Mapped[str] = mapped_column(String(64), default="")
    episode_id: Mapped[str] = mapped_column(String(64), default="")


class IngestStep(Base):
    __tablename__ = "ingest_steps"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingest_jobs.id"))
    document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    detail_json: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GraphEpisode(Base):
    __tablename__ = "graph_episodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingest_jobs.id"))
    group_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source_description: Mapped[str] = mapped_column(String(300))
    reference_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    backend: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[str] = mapped_column(String(64))
