"""Exclusive checkpoint export and independent original-byte verification."""

from hashlib import sha256

from ai_quant_os.candidate_test_checkpoint import (
    capture_candidate_test_checkpoint,
    verify_candidate_test_checkpoint,
)
from ai_quant_os.candidate_test_checkpoint_contracts import CandidateTestCheckpoint
from ai_quant_os.candidate_test_custody import persist
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_jobs import _chain


def checkpoint_command(args):
    if args.command == "checkpoint":
        report = capture_candidate_test_checkpoint(args.root, args.manifest_sha256)
        raw = report.model_dump_json().encode()
        _chain(args.output.absolute().parent)
        if args.output.resolve().is_relative_to(args.root.resolve()):
            raise ValueError("Checkpoint export must remain outside original worker custody")
        persist(args.output, raw)
    else:
        require_digest(args.checkpoint_sha256, prefix="")
        raw = read_repair_image_input(args.input, 65536)
        if sha256(raw).hexdigest() != args.checkpoint_sha256:
            raise ValueError("Checkpoint differs from independent original byte pin")
        report = CandidateTestCheckpoint.model_validate_json(raw)
        if report.model_dump_json().encode() != raw:
            raise ValueError("Canonical original candidate-test checkpoint required")
        verify_candidate_test_checkpoint(args.root, args.manifest_sha256, report)
    return {
        "checkpoint_ref": report.checkpoint_ref,
        "checkpoint_sha256": sha256(raw).hexdigest(),
        "checkpoint": report.checkpoint,
        "historical_observation_verified": args.command == "verify-checkpoint",
        "promotion_allowed": False,
    }
