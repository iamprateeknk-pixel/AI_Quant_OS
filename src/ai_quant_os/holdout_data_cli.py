"""Publish operator-pinned HOLDOUT CSV directly to the isolated Windows service."""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ai_quant_os.holdout_validator_rpc import (
    HoldoutDatasetPublication,
    HoldoutRequest,
    HoldoutRpcError,
    installed_windows_holdout_client,
)
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.research_data_contracts import ResearchDataReceipt
from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog


def main(argv: list[str] | None = None, *, holdout_client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--training-archive", type=Path, required=True)
    publish.add_argument("--input", type=Path, required=True)
    publish.add_argument("--sha256", required=True)
    publish.add_argument("--source-id", required=True)
    args = parser.parse_args(argv)
    try:
        if (
            not args.training_archive.is_absolute()
            or not args.training_archive.is_file()
            or args.training_archive.is_symlink()
        ):
            raise ValueError("An existing absolute TRAINING archive is required")
        raw = read_repair_image_input(args.input, 1048576)
        digest = args.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid independent HOLDOUT digest")
        if sha256(raw).hexdigest() != digest:
            raise ValueError("HOLDOUT CSV differs from its independent SHA-256 pin")
        with ResearchEvidenceCatalog(args.training_archive, training_only=True) as catalog:
            training_receipts = tuple(
                row.model_dump_json()
                for row in sorted(catalog.list(), key=lambda row: row.dataset_ref)
            )
        publication = HoldoutDatasetPublication(
            source_id=args.source_id,
            raw_base64=base64.b64encode(raw).decode("ascii"),
            raw_sha256=digest,
            training_receipts_json=training_receipts,
        )
        request = HoldoutRequest(
            request_id=uuid4().hex,
            operation="HOLDOUT_PUBLISH",
            agent_id="A10",
            agent_version="0.2.0",
            task_id="operator-holdout-publication",
            context_ref="holdout-ingest:" + uuid4().hex,
            evidence_ref="holdout-ingest:" + digest,
            holdout_publication=publication,
        )
        client = holdout_client or installed_windows_holdout_client()
        response = client.call(request)
        receipt = ResearchDataReceipt.model_validate_json(response.receipt_json, strict=True)
        if (
            receipt.model_dump_json() != response.receipt_json
            or receipt.resource_class != "HOLDOUT"
            or receipt.dataset_ref != response.published_ref
            or receipt.raw_sha256 != digest
            or receipt.source_id != args.source_id
        ):
            raise ValueError("HOLDOUT service receipt mismatch")
        print(
            json.dumps(
                {
                    "receipt": receipt.model_dump(mode="json"),
                    "service_publication_verified": True,
                    "isolation_verified": False,
                    "execution_authorized": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except HoldoutRpcError as error:
        parser.exit(2, f"HOLDOUT service rejected publication ({error.error_code}).\n")
    except (ConnectionError, TimeoutError, OSError, sqlite3.Error, ValueError, TypeError):
        parser.exit(2, "HOLDOUT publication refused; no receipt was returned.\n")


if __name__ == "__main__":
    main()
