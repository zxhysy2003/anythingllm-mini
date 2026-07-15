import argparse
import json
from pathlib import Path

from app.services.agent_replay_service import agent_replay_service
from app.tools.registry import create_default_tool_registry


def replay_fixture_file(fixture_path: Path):
    fixture = agent_replay_service.load_fixture(fixture_path)
    return agent_replay_service.replay_fixture(
        fixture,
        create_default_tool_registry(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay deterministic Agent parser, registry, artifact, and metric checks."
    )
    parser.add_argument(
        "fixture", type=Path, help="Path to an Agent replay JSON fixture."
    )
    args = parser.parse_args(argv)

    try:
        report = replay_fixture_file(args.fixture)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
