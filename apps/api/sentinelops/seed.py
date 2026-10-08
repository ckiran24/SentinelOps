from sqlalchemy import select

from .db import SessionLocal
from .models import Runbook
from .runbooks import RUNBOOKS


def seed_runbooks(session):
    for book in RUNBOOKS:
        if not session.scalar(select(Runbook).where(Runbook.id == book["id"])):
            session.add(Runbook(**book))
    session.commit()


def main():
    with SessionLocal() as session:
        seed_runbooks(session)
    print("Seeded curated runbooks; no simulated incident results were precomputed.")


if __name__ == "__main__":
    main()
