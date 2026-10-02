"""Copy locked worker build inputs; never build or execute an image."""

import argparse
import json
from pathlib import Path

from ai_quant_os.repair_image_inputs import prepare_repair_image_context


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an offline repair worker build context")
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = prepare_repair_image_context(args.recipes, args.wheels, args.output)
    except (OSError, ValueError):
        parser.exit(
            2, "Build inputs rejected or output exists; preserve and inspect any partial context.\n"
        )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
