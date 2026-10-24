import hmac
import uuid
from hashlib import sha256

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import clock
from app.db import get_db
from app.models import Actor, Company
from app.settings import settings

bearer = HTTPBearer(auto_error=False)


def issue_token(actor_id: uuid.UUID, ttl_hours: int = 12) -> str:
    exp = int(clock.now().timestamp()) + ttl_hours * 3600
    payload = f"{actor_id}|{exp}"
    sig = hmac.new(settings.session_secret.encode(), payload.encode(), sha256).hexdigest()
    return f"{payload}|{sig}"


def actor_id_from_token(token: str) -> uuid.UUID:
    parts = token.split("|")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="invalid session")
    payload = f"{parts[0]}|{parts[1]}"
    expected = hmac.new(settings.session_secret.encode(), payload.encode(), sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        raise HTTPException(status_code=401, detail="invalid session")
    exp = int(parts[1])
    if int(clock.now().timestamp()) > exp:
        raise HTTPException(status_code=401, detail="session expired")
    return uuid.UUID(parts[0])


def current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Actor:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="authentication required")
    actor_id = actor_id_from_token(credentials.credentials)
    actor = db.get(Actor, actor_id)
    if actor is None or not actor.active:
        raise HTTPException(status_code=401, detail="unknown session")
    return actor


def current_company(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> Company:
    company = db.get(Company, actor.company_id)
    if company is None:
        raise HTTPException(status_code=401, detail="unknown session")
    return company


def require_task(db: Session, actor: Actor, task_id: uuid.UUID):
    from app.models import ProcurementTask

    task = db.scalar(
        select(ProcurementTask).where(
            ProcurementTask.id == task_id,
            ProcurementTask.company_id == actor.company_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="not found")
    return task
