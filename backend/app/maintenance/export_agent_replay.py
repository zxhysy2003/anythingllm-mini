import argparse
import json
from pathlib import Path

from sqlmodel import Session

from app.db.session import engine
from app.services.agent_replay_service import (
    AgentReplayExportError,
    AgentReplayFixture,
    agent_replay_service,
)


def export_fixture(
    invocation_id: str,
    output_path: Path,
    *,
    overwrite: bool = False,
) -> AgentReplayFixture:
    _require_existing_sqlite_database()
    with Session(engine) as session:
        fixture = agent_replay_service.export_invocation(session, invocation_id)
    agent_replay_service.write_fixture(
        fixture,
        output_path,
        overwrite=overwrite,
    )
    return fixture


def _require_existing_sqlite_database() -> None:
    """Avoid SQLite creating an empty database during a read-only export."""
    if not engine.url.drivername.startswith("sqlite"):
        return

    database_path = engine.url.database
    if database_path in {None, "", ":memory:"}:
        raise AgentReplayExportError(
            "replay export requires a persisted SQLite database file"
        )
    if not Path(database_path).is_file():
        raise AgentReplayExportError(
            f"SQLite database file does not exist: {database_path}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export one persisted Agent invocation as a replay fixture."
    )
    parser.add_argument(
        "invocation_id", help="Persisted agent_invocations.id to export."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Explicit local JSON file path for the exported fixture.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output fixture file.",
    )
    args = parser.parse_args(argv)

    try:
        fixture = export_fixture(
            args.invocation_id,
            args.output,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(
        json.dumps(
            {
                "output": str(args.output),
                "schema_version": fixture.schema_version,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
