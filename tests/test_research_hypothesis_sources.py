import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ai_quant_os.agent_context import ContextCompiler, SourceBinding
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_evidence_catalog import ResearchEvidenceCatalog
from ai_quant_os.research_hypothesis_contracts import (
    HypothesisSpec,
    hypothesis_recipe,
    read_hypothesis_inputs,
)
from ai_quant_os.research_hypothesis_sources import hypothesis_sources
from test_research_hypothesis_api import NOW, training_csv


def training(tmp_path):
    with ResearchEvidenceCatalog(tmp_path / "data.sqlite3") as store:
        store.publish(
            training_csv().encode(),
            source_id="development",
            resource_class="TRAINING",
            received_at=NOW,
        )
        return store.training(load_agent_catalog(), "A08", "0.2.0", scope="SANDBOX")


def compile_sources(sources):
    return asyncio.run(
        ContextCompiler(
            load_agent_catalog(),
            sources,
            clock=lambda: NOW,
            max_input_bytes=65536,
            max_context_bytes=100000,
        ).compile("A08", "0.2.0", task_id="recipe", scope="SANDBOX")
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"training_dataset_count": 0},
        {"hypotheses": ()},
        {"contradictions": ()},
        {"deferred_hypothesis_count": 1},
    ],
)
def test_hypothesis_output_rejects_changed_retained_source_claims(tmp_path, changes):
    context = compile_sources(hypothesis_sources(training(tmp_path), (), (), now=NOW))
    output = HypothesisSpec(
        task_id=context.task_id,
        input_refs=context.input_refs,
        evidence_refs=context.input_refs,
        created_at=context.created_at,
        valid_until=context.valid_until,
        trace_id="fixture",
        **hypothesis_recipe(context),
    )
    output.require_context(context)
    forged = HypothesisSpec.model_validate({**output.model_dump(), **changes})
    with pytest.raises(ValueError, match="retained source"):
        forged.require_context(context)


def test_training_resource_cannot_be_relabelled_general_in_a_ready_context(tmp_path):
    sources = hypothesis_sources(training(tmp_path), (), (), now=NOW)
    original = sources["historical_catalog"]
    sources["historical_catalog"] = SourceBinding(original.tool, "GENERAL", original.fetch)
    context = compile_sources(sources)
    assert context.status == "READY"
    with pytest.raises(ValueError, match="classification"):
        read_hypothesis_inputs(context)


def test_source_capture_rejects_dataset_bytes_that_differ_from_retained_receipt(tmp_path):
    ((receipt, dataset),) = training(tmp_path)
    altered = replace(dataset.bars[0], close=Decimal("1.0999"), low=Decimal("1.0999"))
    with pytest.raises(ValueError, match="differs"):
        hypothesis_sources(((receipt, BarDataset((altered, *dataset.bars[1:]))),), (), (), now=NOW)


def test_future_training_receipt_or_collection_overflow_is_rejected(tmp_path):
    rows = training(tmp_path)
    with pytest.raises(ValueError, match="future"):
        hypothesis_sources(rows, (), (), now=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="bound"):
        hypothesis_sources(rows * 65, (), (), now=NOW)
