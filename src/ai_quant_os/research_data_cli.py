"""Register bounded operator-supplied research CSV and inspect classified metadata."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("csv", type=Path)
    register.add_argument("--source-id", required=True)
    register.add_argument("--resource-class", required=True, choices=("TRAINING",))
    commands.add_parser("list")
    args = parser.parse_args()
    try:
        with ResearchEvidenceCatalog(args.catalog) as catalog:
            if args.command == "register":
                with args.csv.open("rb") as stream:
                    raw = stream.read(1048577)
                receipt = catalog.publish(
                    raw,
                    source_id=args.source_id,
                    resource_class=args.resource_class,
                    received_at=datetime.now(timezone.utc),
                )
                output = receipt.model_dump(mode="json")
            else:
                output = [row.model_dump(mode="json") for row in catalog.list()]
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Research data catalog refused: {error}\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
