import argparse
import asyncio
import json

from sqlmodel import Session

from app.db.init_db import create_db_and_tables
from app.db.session import engine
from app.services.document_consistency_service import (
    DocumentConsistencyReport,
    document_consistency_service,
)


async def run_reconcile(repair: bool) -> DocumentConsistencyReport:
    create_db_and_tables(engine)
    with Session(engine) as session:
        return await document_consistency_service.reconcile(session, repair=repair)


def exit_code_for_report(report: DocumentConsistencyReport) -> int:
    if not report.issues:
        return 0
    if not report.repair:
        return 1
    if any(not issue.repairable for issue in report.issues):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check workspace document storage and Chroma consistency."
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Delete repairable orphan storage directories and orphan vectors.",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(run_reconcile(repair=args.repair))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(report.model_dump_json(indent=2))
    return exit_code_for_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
