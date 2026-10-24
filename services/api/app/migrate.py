from pathlib import Path

from sqlalchemy import text

from app.db import engine

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ADVISORY_LOCK = 814201


def _split_sql(script: str) -> list[str]:
    """Split SQL on semicolons that are outside dollar-quoted function bodies."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    in_dollar = False
    while i < len(script):
        if script.startswith("$$", i):
            in_dollar = not in_dollar
            current.append("$$")
            i += 2
            continue
        char = script[i]
        if char == ";" and not in_dollar:
            statement = "".join(current).strip()
            if statement and not statement.startswith("--"):
                parts.append(statement)
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    cleaned = []
    for statement in parts:
        lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
        text_statement = "\n".join(lines).strip()
        if text_statement:
            cleaned.append(text_statement)
    return cleaned


def apply() -> list[str]:
    applied: list[str] = []
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK})
        conn.commit()
        try:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            conn.commit()
            for path in sorted(MIGRATIONS.glob("*.sql")):
                version = path.stem
                found = conn.execute(
                    text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                    {"version": version},
                ).scalar()
                if found:
                    continue
                script = path.read_text()
                for statement in _split_sql(script):
                    conn.execute(text(statement))
                conn.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )
                conn.commit()
                applied.append(version)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK})
            conn.commit()
    return applied


if __name__ == "__main__":
    print("applied", apply())
