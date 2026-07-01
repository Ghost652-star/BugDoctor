from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from bugdoctor.app import run_app


def main() -> None:
    parser = argparse.ArgumentParser(description="BugDoctor — bug diagnosis agent")
    parser.add_argument(
        "--project",
        type=Path,
        default=Path("."),
        help="Target project directory to diagnose",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional config.yaml path",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="Start a new session (skip session picker)",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Resume a specific session id",
    )
    args = parser.parse_args()
    asyncio.run(
        run_app(
            args.project.resolve(),
            args.config,
            new_session=args.new,
            session_id=args.session,
        )
    )


if __name__ == "__main__":
    main()
