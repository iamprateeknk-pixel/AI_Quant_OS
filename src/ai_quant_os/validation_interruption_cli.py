"""Exclusive canonical report files with independently pinned historical verification."""

from hashlib import sha256

from ai_quant_os.candidate_test_custody import persist
from ai_quant_os.repair_image_inputs import read_repair_image_input
from ai_quant_os.strategy_image_content import require_digest
from ai_quant_os.strategy_prefix_jobs import _chain
from ai_quant_os.validation_interruption import (
    capture_validation_interruption,
    verify_validation_interruption,
)
from ai_quant_os.validation_interruption_contracts import ValidationInterruption


def interruption_command(package, args):
    if args.command == "interruption":
        record = capture_validation_interruption(
            package, root=args.root, job_sha256=args.job_sha256
        )
        raw = record.model_dump_json().encode()
        _chain(args.output.absolute().parent)
        # An export is exclusive: it cannot replace journal/source or an older observation.
        persist(args.output, raw)
    else:
        require_digest(args.interruption_sha256, prefix="")
        raw = read_repair_image_input(args.input, 65536)
        if sha256(raw).hexdigest() != args.interruption_sha256:
            raise ValueError("Interruption differs from independent original byte pin")
        record = ValidationInterruption.model_validate_json(raw)
        if record.model_dump_json().encode() != raw:
            raise ValueError("Canonical original interruption observation required")
        verify_validation_interruption(
            package, root=args.root, job_sha256=args.job_sha256, record=record
        )
    return {
        "interruption_ref": record.interruption_ref,
        "interruption_sha256": sha256(raw).hexdigest(),
        "historical_observation_verified": args.command == "verify-interruption",
        "promotion_allowed": False,
    }
