import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def interval_contains(start: datetime | None, end: datetime | None, moment: datetime) -> bool:
    start = aware(start)
    end = aware(end)
    moment = aware(moment)
    if start is not None and moment < start:
        return False
    if end is not None and moment >= end:
        return False
    return True


def exclusion_reason(version, grants, *, organization_id: str, role: str, as_of: datetime, mode: str, hidden_version_ids: set[str]) -> str | None:
    artifact = version.artifact
    if artifact.organization_id != organization_id:
        return "OUTSIDE_ORGANIZATION"
    if version.id in hidden_version_ids and mode == "pre_decision":
        return "NOT_IN_SNAPSHOT"
    if version.availability_status != "verified" or version.available_from is None:
        return "UNKNOWN_AVAILABILITY"
    if not interval_contains(version.available_from, version.available_to, as_of):
        if version.available_from and aware(as_of) < aware(version.available_from):
            return "FUTURE_INFORMATION"
        return "NOT_IN_SNAPSHOT"
    allowed = [
        grant
        for grant in grants
        if grant.role == role and grant.permission == "read" and interval_contains(grant.grant_from, grant.grant_to, as_of)
    ]
    if not allowed:
        return "INSUFFICIENT_PERMISSION"
    return None
