import asyncio

from app.maintenance import reconcile_documents
from app.services.document_consistency_service import DocumentConsistencyReport


class FakeSession:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeConsistencyService:
    def __init__(self, events):
        self.events = events

    async def reconcile(self, session, repair):
        self.events.append(("reconcile", session.engine, repair))
        return DocumentConsistencyReport(
            repair=repair,
            counts={},
            issues=[],
            repaired_actions=[],
        )


def test_run_reconcile_creates_tables_before_scanning(monkeypatch):
    events = []

    def fake_create_db_and_tables(engine):
        events.append(("create_tables", engine))

    monkeypatch.setattr(
        reconcile_documents,
        "create_db_and_tables",
        fake_create_db_and_tables,
    )
    monkeypatch.setattr(reconcile_documents, "Session", FakeSession)
    monkeypatch.setattr(
        reconcile_documents,
        "document_consistency_service",
        FakeConsistencyService(events),
    )

    report = asyncio.run(reconcile_documents.run_reconcile(repair=True))

    assert report.repair is True
    assert events == [
        ("create_tables", reconcile_documents.engine),
        ("reconcile", reconcile_documents.engine, True),
    ]
