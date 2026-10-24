import time
from datetime import datetime, timedelta, timezone

from app.settings import settings


class Clock:
    """Business clock.

    DEMO_CLOCK anchors the calendar (so the November 15 fixture stays coherent)
    while elapsed wall time still advances leases and backoff. Tests can freeze it.
    Signature checks for Spectrum use the wall clock, not this clock.
    """

    def __init__(self) -> None:
        self._anchor_wall = time.time()
        self._anchor_demo = self._parse_anchor(settings.demo_clock)
        self._frozen: datetime | None = None

    @staticmethod
    def _parse_anchor(raw: str) -> datetime | None:
        if not raw:
            return None
        text = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def now(self) -> datetime:
        if self._frozen is not None:
            return self._frozen
        if self._anchor_demo is None:
            return datetime.now(timezone.utc)
        elapsed = time.time() - self._anchor_wall
        return self._anchor_demo + timedelta(seconds=elapsed)

    def today(self):
        return self.now().date()

    def freeze(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        self._frozen = moment.astimezone(timezone.utc)

    def unfreeze(self) -> None:
        self._frozen = None


clock = Clock()
