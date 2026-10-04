"""Operator-only static worker image commands; no daemon, push or source execution."""

import argparse
from pathlib import Path
import tarfile

from ai_quant_os.strategy_image_archive import assemble_strategy_worker_image
from ai_quant_os.strategy_image_verification import verify_strategy_worker_image


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble or check a pinned static worker OCI archive"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble")
    verify = commands.add_parser("verify")
    for command in (assemble, verify):
        command.add_argument("--base-metadata", required=True, type=Path)
        command.add_argument("--base-manifest-digest", required=True)
        command.add_argument("--worker-sha256", required=True)
    assemble.add_argument("--worker", required=True, type=Path)
    assemble.add_argument("--base-blobs", required=True, type=Path)
    assemble.add_argument("--output", required=True, type=Path)
    verify.add_argument("--archive", required=True, type=Path)
    verify.add_argument("--archive-sha256", required=True)
    verify.add_argument("--manifest-digest", required=True)
    verify.add_argument("--config-digest", required=True)
    args = parser.parse_args()
    try:
        common = {
            "base_metadata": args.base_metadata,
            "base_manifest_digest": args.base_manifest_digest,
            "worker_sha256": args.worker_sha256,
        }
        if args.command == "assemble":
            receipt = assemble_strategy_worker_image(
                args.worker, base_blobs=args.base_blobs, output=args.output, **common
            )
        else:
            receipt = verify_strategy_worker_image(
                args.archive,
                archive_sha256=args.archive_sha256,
                manifest_digest=args.manifest_digest,
                config_digest=args.config_digest,
                **common,
            )
    except (OSError, ValueError, tarfile.TarError):
        parser.exit(
            2,
            "Static worker image refused; inspect independent pins, bounded inputs and retained partial output.\n",
        )
    print(receipt.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
