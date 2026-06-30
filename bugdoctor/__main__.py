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
    args = parser.parse_args()
    asyncio.run(run_app(args.project.resolve(), args.config))


if __name__ == "__main__":
    main()
