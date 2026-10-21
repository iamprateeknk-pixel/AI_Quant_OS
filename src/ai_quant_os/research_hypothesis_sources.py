"""Complete checked training, prior hypothesis and archival paper-world evidence."""

from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Context, localcontext

from ai_quant_os.agent_context import DatasetResourceCatalog, EvidenceRecord, SourceBinding
from ai_quant_os.graph_ledger import GraphRecord
from ai_quant_os.macro_sources import MacroWorldEvidence
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_contracts import ResearchDataReceipt
from ai_quant_os.research_hypothesis_contracts import (
    ArchivedSpread,
    DirectionEvidence,
    HistoricalResearchEvidence,
    HypothesisSpec,
    HypothesisSpecV3,
    ResearchMemoryEvidence,
    ResearchWorldEvidence,
    ResearchWorldSummary,
)
from ai_quant_os.research_hypothesis_policy import direction_counts
from ai_quant_os.world_state import WorldState, fingerprint

HYPOTHESIS_INPUT_BYTES = 65536
HYPOTHESIS_CONTEXT_BYTES = 100000


def hypothesis_sources(
    training: tuple[tuple[ResearchDataReceipt, BarDataset], ...],
    records: tuple[GraphRecord, ...],
    worlds: tuple[WorldState, ...],
    *,
    now: datetime,
    agent_version: str = "0.2.0",
    reserved_signatures: tuple[str, ...] = (),
) -> dict[str, SourceBinding]:
    if (
        agent_version not in {"0.2.0", "0.3.0"}
        or len(training) > 64
        or len(records) > 256
        or len(worlds) > 64
        or len(reserved_signatures) > 512
        or reserved_signatures != tuple(sorted(set(reserved_signatures)))
    ):
        raise ValueError("Research source collection exceeds bound")
    histories = []
    for receipt, dataset in training:
        receipt = ResearchDataReceipt.model_validate_json(receipt.model_dump_json())
        with localcontext(Context(prec=28)):
            if (
                receipt.dataset_fingerprint != dataset.fingerprint
                or receipt.row_count != len(dataset.bars)
                or dataset.duplicate_rows
                or any(row.instrument_id != receipt.instrument_id for row in dataset.bars)
                or dataset.bars[0].observed_at != receipt.first_observed_at
                or dataset.bars[-1].observed_at != receipt.last_observed_at
            ):
                raise ValueError("Research dataset differs from retained receipt")
        pairs, same = direction_counts(tuple(row.close for row in dataset.bars))
        histories.append(
            DirectionEvidence(receipt=receipt, eligible_pairs=pairs, same_direction_pairs=same)
        )
    historical = HistoricalResearchEvidence(
        as_of=now,
        datasets=tuple(histories),
        collection_sha256=fingerprint([row.model_dump(mode="json") for row in histories]),
    )
    signatures = set()
    completed = []
    for record in records:
        record = GraphRecord.model_validate_json(record.model_dump_json())
        if (record.receipt.agent_id, record.receipt.agent_version) not in {
            ("A08", "0.2.0"),
            ("A08", "0.3.0"),
        }:
            raise ValueError("Research memory contains unsupported agent output")
        if record.receipt.created_at > now:
            raise ValueError("Research memory contains future observations")
        if record.receipt.status != "COMPLETED":
            continue
        output_type = (
            HypothesisSpec if record.receipt.agent_version == "0.2.0" else HypothesisSpecV3
        )
        output = output_type.model_validate_json(record.receipt.output_json)
        output.require_context(record.checkpoint.context)
        signatures.update(row.signature for row in output.hypotheses)
        completed.append(record)
    reserved_signatures = tuple(sorted(set(reserved_signatures) - signatures))
    memory = ResearchMemoryEvidence(
        as_of=now,
        completed_run_count=len(completed),
        known_signatures=tuple(sorted(signatures)),
        reserved_signatures=reserved_signatures,
        active_reservation_count=len(reserved_signatures),
        coverage=(
            "CHECKED_COMPLETED_OUTPUTS_AND_ACTIVE_RESERVATIONS"
            if reserved_signatures
            else "CHECKED_A08_0_2_COMPLETED_GRAPH_OUTPUTS"
        ),
        collection_sha256=fingerprint([row.model_dump(mode="json") for row in completed]),
    )
    summaries = []
    for world in worlds:
        world = MacroWorldEvidence(world=world).world
        summaries.append(
            ResearchWorldSummary(
                world_ref=world.world_state_version,
                as_of=world.as_of,
                valid_until=world.valid_until,
                spreads=tuple(
                    ArchivedSpread(
                        instrument_id=row.instrument_id,
                        quote_status=row.quote_status,
                        spread_bps=row.spread_bps,
                    )
                    for row in world.instruments
                ),
            )
        )
    archives = ResearchWorldEvidence(
        as_of=now,
        snapshots=tuple(summaries),
        collection_sha256=fingerprint([asdict(row) for row in worlds]),
    )
    sources = {}
    dataset_catalog = DatasetResourceCatalog.from_receipts(
        tuple(receipt for receipt, _ in training)
    )
    for name, payload, tool, resource in (
        ("historical_catalog", historical, "HISTORY_READ", "TRAINING"),
        ("research_memory", memory, "RESEARCH_MEMORY_READ", "TRAINING"),
        ("world_state_archives", archives, "WORLD_STATE_READ", "GENERAL"),
    ):
        record = EvidenceRecord(
            source_id=f"paper-research:{name}:{fingerprint(payload.model_dump(mode='json'))}",
            as_of=now,
            valid_until=now + timedelta(seconds=5),
            trusted=True,
            payload_json=payload.model_dump_json(),
        )

        async def fetch(record=record):
            return record

        sources[name] = SourceBinding(
            tool,
            resource,
            fetch,
            resource_refs=tuple(receipt.dataset_ref for receipt, _ in training)
            if resource == "TRAINING"
            else (),
            resource_catalog=dataset_catalog if resource == "TRAINING" else None,
        )
    return sources
