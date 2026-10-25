from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Actor, ArtifactVersion, Case, Snapshot
from app.temporal import canonical, exclusion_reason, sha256_text


class AccessError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def get_actor(db: Session, actor_id: str) -> Actor:
    actor = db.get(Actor, actor_id)
    if actor is None:
        raise AccessError("INSUFFICIENT_PERMISSION")
    return actor


def get_case(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    return case


def versions(db: Session) -> list[ArtifactVersion]:
    return list(db.scalars(select(ArtifactVersion).options(selectinload(ArtifactVersion.artifact), selectinload(ArtifactVersion.grants))).all())


def hidden_ids(case: Case, mode: str) -> set[str]:
    if mode == "pre_decision":
        return {case.decision_version_id}
    return set()


def classify(version: ArtifactVersion, actor: Actor, as_of: datetime, mode: str, hidden: set[str]) -> str | None:
    return exclusion_reason(
        version,
        version.grants,
        organization_id=actor.organization_id,
        role=actor.role,
        as_of=as_of,
        mode=mode,
        hidden_version_ids=hidden,
    )


def create_snapshot(db: Session, *, case_id: str, actor_id: str, as_of: datetime, mode: str) -> Snapshot:
    if mode not in {"audit", "pre_decision"}:
        raise AccessError("NOT_IN_SNAPSHOT")
    actor = get_actor(db, actor_id)
    case = get_case(db, case_id)
    if actor.organization_id != case.organization_id:
        raise AccessError("OUTSIDE_ORGANIZATION")
    existing = db.scalar(
        select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.actor_id == actor_id,
            Snapshot.as_of == as_of,
            Snapshot.mode == mode,
        )
    )
    if existing is not None:
        return existing
    hidden = hidden_ids(case, mode)
    allowed: list[str] = []
    counts: dict[str, int] = {}
    for version in versions(db):
        if version.artifact.organization_id != actor.organization_id:
            continue
        reason = classify(version, actor, as_of, mode, hidden)
        if reason is None:
            allowed.append(version.id)
        else:
            counts[reason] = counts.get(reason, 0) + 1
    allowed.sort()
    manifest_hash = sha256_text(
        canonical({"org": actor.organization_id, "actor": actor.id, "as_of": as_of.isoformat(), "mode": mode, "allowed": allowed})
    )
    snapshot = Snapshot(
        id=f"snap_{uuid4().hex[:16]}",
        case_id=case.id,
        organization_id=actor.organization_id,
        actor_id=actor.id,
        as_of=as_of,
        mode=mode,
        allowed_manifest_json=allowed,
        excluded_counts_json=counts,
        manifest_hash=manifest_hash,
        created_at=datetime.now(timezone.utc),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def require_snapshot(db: Session, snapshot_id: str) -> Snapshot:
    snapshot = db.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    return snapshot


def load_version(db: Session, version_id: str) -> ArtifactVersion | None:
    return db.scalar(
        select(ArtifactVersion)
        .where(ArtifactVersion.id == version_id)
        .options(selectinload(ArtifactVersion.artifact), selectinload(ArtifactVersion.grants), selectinload(ArtifactVersion.facts))
    )


def denial_code(db: Session, snapshot: Snapshot, version_id: str) -> str:
    version = load_version(db, version_id)
    if version is None:
        return "NOT_IN_SNAPSHOT"
    actor = get_actor(db, snapshot.actor_id)
    case = get_case(db, snapshot.case_id)
    reason = classify(version, actor, snapshot.as_of, snapshot.mode, hidden_ids(case, snapshot.mode))
    return reason or "NOT_IN_SNAPSHOT"


def guarded_version(db: Session, snapshot: Snapshot, version_id: str) -> ArtifactVersion:
    if version_id not in set(snapshot.allowed_manifest_json):
        raise AccessError(denial_code(db, snapshot, version_id))
    version = load_version(db, version_id)
    if version is None:
        raise AccessError("NOT_IN_SNAPSHOT")
    return version
