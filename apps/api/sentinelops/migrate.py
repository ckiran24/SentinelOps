"""Run versioned Alembic migrations and provision an optional vector table."""

import sys
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command

from .config import get_settings
from .db import engine


def main():
    root = Path(__file__).resolve().parent.parent
    if not (root / "alembic.ini").is_file():
        root = Path(sys.prefix) / "share" / "sentinelops"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    if get_settings().enable_pgvector:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("ENABLE_PGVECTOR requires PostgreSQL with vector extension")
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS runbook_embeddings ("
                    "runbook_id VARCHAR(40) PRIMARY KEY REFERENCES runbooks(id), "
                    "embedding vector(1536) NOT NULL, "
                    "model TEXT NOT NULL, content_hash VARCHAR(64) NOT NULL)"
                )
            )
    print("Database migrated. Vector schema is optional; keyword retrieval is the baseline.")


if __name__ == "__main__":
    main()
