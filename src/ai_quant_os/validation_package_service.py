"""Prepare exact archival inputs using trusted stores; never run proposed candidate bytes."""

import base64
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from ai_quant_os.contracts import _require_aware
from ai_quant_os.graph_ledger import GraphRecord
from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
from ai_quant_os.strategy_build_store import StrategyBuildStore
from ai_quant_os.strategy_builder_agent import StrategyCandidate
from ai_quant_os.validation_package_contracts import (
    SDK_PATHS,
    DatasetSnapshot,
    SDKFile,
    ValidationPackage,
    ValidationPlan,
)
from ai_quant_os.validation_package_store import ValidationPackageStore


def capture_sdk_files() -> tuple[SDKFile, ...]:
    files = []
    for path in SDK_PATHS:
        with Path(__file__).with_name(path.split("/")[-1]).open("rb") as source:
            raw = source.read(65537)
        if not 0 < len(raw) <= 65536:
            raise ValueError("Validation SDK source exceeds bound")
        files.append(
            SDKFile(
                path=path, raw_base64=base64.b64encode(raw).decode(), sha256=sha256(raw).hexdigest()
            )
        )
    return tuple(files)


class ValidationPreparation:
    def __init__(
        self,
        builds: StrategyBuildStore,
        catalog: ResearchEvidenceCatalog,
        archive: ValidationPackageStore,
    ) -> None:
        self._builds, self._catalog, self._archive = builds, catalog, archive

    def prepare(
        self, graph: GraphRecord, holdout_ref: str, plan: ValidationPlan, *, now: datetime
    ) -> ValidationPackage:
        _require_aware(now)
        graph = GraphRecord.model_validate_json(graph.model_dump_json())
        if (graph.receipt.agent_id, graph.receipt.agent_version, graph.receipt.status) != (
            "A09",
            "0.2.0",
            "COMPLETED",
        ):
            raise ValueError("Validation preparation requires a completed A09 graph")
        output = StrategyCandidate.model_validate_json(graph.receipt.output_json)
        output.require_context(graph.checkpoint.context)
        candidate = self._builds.get_candidate(output.candidate_ref)
        if candidate is None:
            raise KeyError("Retained candidate not found")
        training, holdout = self._catalog.validation_pair(
            candidate.review.hypothesis.dataset_ref, holdout_ref
        )
        package = ValidationPackage(
            graph=graph,
            candidate=candidate,
            training=DatasetSnapshot(
                receipt=training[0], raw_base64=base64.b64encode(training[1]).decode()
            ),
            holdout=DatasetSnapshot(
                receipt=holdout[0], raw_base64=base64.b64encode(holdout[1]).decode()
            ),
            sdk_files=capture_sdk_files(),
            plan=plan,
            captured_at=now.astimezone(timezone.utc),
        )
        return self._archive.put(package)
