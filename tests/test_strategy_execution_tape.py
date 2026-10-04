"""Operator-pinned completion metadata assembly and actual native numeric replay."""

from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from ai_quant_os.candidate_invocations import prepare_invocation
from ai_quant_os.candidate_invocation_contracts import CandidateTargetOutput
from ai_quant_os.strategy_container_contracts import (
    StrategyContainerOwnership,
    StrategyContainerRecord,
)
from ai_quant_os.strategy_prefix_execution_contracts import (
    StrategyContainerExit,
    StrategyPrefixExecutionReceipt,
    strategy_start_request,
)
from test_signal_tape_replay import package as package

IMAGE = "sha256:" + "1" * 64
WORKER = "2" * 64


def module():
    assert importlib.util.find_spec("ai_quant_os.strategy_execution_tape"), (
        "Completion-to-native replay bridge is required"
    )
    return importlib.import_module("ai_quant_os.strategy_execution_tape")


@pytest.fixture
def receipts(package, tmp_path):
    result = []
    for index in range(2, package.holdout.receipt.row_count):
        invocation = prepare_invocation(package, index)
        owner = f"{index:032x}"
        name = "strategy-job-" + owner
        ownership = StrategyContainerOwnership(
            owner=owner,
            job_path=str(tmp_path / name),
            job_name=name,
            invocation_ref=invocation.invocation_ref,
            stage_sha256="0" * 64,
            image_id=IMAGE,
            worker_sha256=WORKER,
        )
        target = CandidateTargetOutput(
            invocation_ref=invocation.invocation_ref,
            target=1 if index == 2 else -1 if index == 3 else 0,
        )
        cid = f"{index:064x}"
        create = f"{index + 100:064x}"
        exit = StrategyContainerExit(
            container_id=cid,
            status="exited",
            running=False,
            paused=False,
            restarting=False,
            oom_killed=False,
            dead=False,
            pid=0,
            exit_code=0,
            error="",
            started_at="2026-10-05T00:00:00Z",
            finished_at="2026-10-05T00:00:01Z",
        )
        result.append(
            StrategyPrefixExecutionReceipt(
                ownership=ownership,
                create_sha256=create,
                start_sha256=sha256(strategy_start_request(ownership, create, cid)).hexdigest(),
                output_sha256=sha256(target.model_dump_json().encode()).hexdigest(),
                exit_metadata_sha256=exit.metadata_sha256,
                target=target,
                exit=exit,
                removal=StrategyContainerRecord(
                    ownership=ownership, state="REMOVED", container_id=cid
                ),
            )
        )
    return tuple(result)


def assemble(package, receipts):
    return module().assemble_strategy_execution_tape(
        package, receipts, image_id=IMAGE, worker_sha256=WORKER
    )


def test_complete_receipts_assemble_canonical_tape_without_importing_proposed_source(
    package, receipts, monkeypatch
):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "candidate", "Proposed candidate must not execute on the host"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    collection = assemble(package, receipts)
    assert collection.package_ref == package.package_ref
    assert tuple(row.target for row in collection.tape.decisions)[:4] == (0, 0, 1, -1)
    assert collection.execution_verified is False and collection.promotion_allowed is False
    assert collection == assemble(package, tuple(reversed(receipts)))
    collection.require_package(package)


def test_missing_extra_duplicate_and_foreign_receipts_are_refused(package, receipts):
    for invalid in (receipts[:-1], receipts + receipts[:1], receipts[:-1] + receipts[:1]):
        with pytest.raises(ValueError):
            assemble(package, invalid)
    row = receipts[0].model_dump(mode="json")
    row["target"]["invocation_ref"] = "candidate-invocation:" + "f" * 64
    row["ownership"]["invocation_ref"] = row["target"]["invocation_ref"]
    row["removal"]["ownership"] = row["ownership"]
    row["output_sha256"] = sha256(
        CandidateTargetOutput.model_validate(row["target"]).model_dump_json().encode()
    ).hexdigest()
    owner = StrategyContainerOwnership.model_validate(row["ownership"])
    row["start_sha256"] = sha256(
        strategy_start_request(owner, row["create_sha256"], row["exit"]["container_id"])
    ).hexdigest()
    foreign = StrategyPrefixExecutionReceipt.model_validate(row)
    with pytest.raises(ValueError):
        assemble(package, (foreign, *receipts[1:]))


@pytest.mark.parametrize("field", ["owner", "job_path", "container_id"])
def test_reused_container_custody_cannot_cover_two_different_decisions(package, receipts, field):
    assert len(receipts) >= 2
    row = receipts[1].model_dump(mode="json")
    first = receipts[0]
    if field == "owner":
        row["ownership"]["owner"] = first.ownership.owner
    elif field == "job_path":
        row["ownership"]["job_path"] = first.ownership.job_path
        row["ownership"]["job_name"] = first.ownership.job_name
    else:
        row["exit"]["container_id"] = first.exit.container_id
        row["exit_metadata_sha256"] = StrategyContainerExit.model_validate(
            row["exit"]
        ).metadata_sha256
        row["removal"]["container_id"] = first.exit.container_id
    row["removal"]["ownership"] = row["ownership"]
    owner = StrategyContainerOwnership.model_validate(row["ownership"])
    row["start_sha256"] = sha256(
        strategy_start_request(owner, row["create_sha256"], row["exit"]["container_id"])
    ).hexdigest()
    altered = StrategyPrefixExecutionReceipt.model_validate(row)
    with pytest.raises(ValueError):
        assemble(package, (receipts[0], altered, *receipts[2:]))


@pytest.mark.parametrize("pin", ["image_id", "worker_sha256"])
def test_independent_source_or_image_pin_cannot_be_substituted(package, receipts, pin):
    pins = {"image_id": IMAGE, "worker_sha256": WORKER}
    pins[pin] = ("sha256:" if pin == "image_id" else "") + "3" * 64
    with pytest.raises(ValueError):
        module().assemble_strategy_execution_tape(package, receipts, **pins)


def test_actual_native_replay_and_checked_restore_use_only_numeric_targets(package, receipts):
    c = assemble(package, receipts)
    report = module().replay_strategy_execution_tape(package, c)
    assert report.collection == c and report.replay.engine_version == "1.221.0"
    assert report.replay.native_order_count >= 2 and report.replay.fills[1].quantity == 2
    assert report.replay.candidate_code_executed is False
    assert report.execution_verified is False and report.promotion_allowed is False
    module().verify_strategy_execution_replay(package, report)
    assert report.report_ref.startswith("strategy-execution-replay:")


def test_collection_rehash_cannot_hide_tape_target_substitution(package, receipts):
    c = assemble(package, receipts)
    raw = c.model_dump(mode="json")
    raw["tape"]["decisions"][2]["target"] = -1
    altered = type(c).model_validate(raw)
    with pytest.raises(ValueError):
        altered.require_package(package)


def test_portable_completion_metadata_can_be_validated_without_local_files(package, receipts):
    row = receipts[0].model_dump(mode="json")
    name = row["ownership"]["job_name"]
    from os import name as platform_name

    row["ownership"]["job_path"] = (
        ("/metadata/" + name) if platform_name == "nt" else ("D:\\metadata\\" + name)
    )
    row["removal"]["ownership"] = row["ownership"]
    portable = StrategyPrefixExecutionReceipt.model_validate(row)
    c = assemble(package, (portable, *receipts[1:]))
    c.require_package(package)


def test_portable_metadata_never_authorizes_a_foreign_local_journal(receipts, tmp_path):
    from ai_quant_os.strategy_container_journal import StrategyContainerJournal

    row = receipts[0].ownership.model_dump(mode="json")
    row["job_path"] = "/foreign/" + row["job_name"]
    owner = StrategyContainerOwnership.model_validate(row)
    with StrategyContainerJournal(tmp_path) as journal:
        with pytest.raises(ValueError, match="different root"):
            journal.reserve(owner)
        assert journal.event_count == 0


def test_retained_collection_and_replay_schemas_are_exact_and_disallow_authority(package, receipts):
    import jsonschema

    c = assemble(package, receipts)
    report = module().replay_strategy_execution_tape(package, c, cost_scenario="DECLARED_STRESS")
    for value, name in ((c, "strategy-execution-tape"), (report, "strategy-execution-replay")):
        path = Path(__file__).resolve().parents[1] / "docs/schemas" / (name + ".schema.json")
        assert path.exists(), "Retained completion bridge wire schema required"
        schema = json.loads(path.read_text())
        assert schema == type(value).model_json_schema()
        jsonschema.validate(value.model_dump(mode="json"), schema)
        forged = value.model_dump(mode="json")
        forged["execution_verified"] = True
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(forged, schema)
    corrupted = report.model_dump(mode="json")
    corrupted["replay"]["net_pnl"] = "999999"
    with pytest.raises(ValueError):
        module().verify_strategy_execution_replay(package, type(report).model_validate(corrupted))


def test_operator_cli_requires_independent_collection_hash_and_exclusive_output(
    package, receipts, tmp_path, monkeypatch, capsys
):
    import sys
    from ai_quant_os.validation_package_store import ValidationPackageStore

    c = assemble(package, receipts)
    archive = tmp_path / "packages.sqlite3"
    with ValidationPackageStore(archive) as store:
        store.put(package)
    source = tmp_path / "completions.json"
    source.write_bytes(c.model_dump_json().encode())
    output = tmp_path / "replay.json"
    assert importlib.util.find_spec("ai_quant_os.strategy_execution_tape_cli"), (
        "Operator completion replay CLI required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_execution_tape_cli")
    args = [
        "completions",
        "replay",
        "--archive",
        str(archive),
        "--package-ref",
        package.package_ref,
        "--collection",
        str(source),
        "--collection-sha256",
        sha256(source.read_bytes()).hexdigest(),
        "--image-id",
        IMAGE,
        "--worker-sha256",
        WORKER,
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 0
    report = json.loads(output.read_bytes())
    assert report["execution_verified"] is False
    assert json.loads(capsys.readouterr().out)["status"] == "UNATTESTED_COMPLETION_NATIVE_REPLAY"
    original = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert output.read_bytes() == original
    args[args.index("--collection-sha256") + 1] = "f" * 64
    args[args.index("--output") + 1] = str(tmp_path / "bad.json")
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and not (tmp_path / "bad.json").exists()


def test_operator_cli_assembles_complete_inventory_under_independent_byte_pin(
    package, receipts, tmp_path, monkeypatch
):
    import sys
    from ai_quant_os.validation_package_store import ValidationPackageStore

    archive = tmp_path / "packages.sqlite3"
    with ValidationPackageStore(archive) as store:
        store.put(package)
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps([r.model_dump(mode="json") for r in receipts]))
    output = tmp_path / "collection.json"
    assert importlib.util.find_spec("ai_quant_os.strategy_execution_tape_cli"), (
        "Operator receipt assembly CLI required"
    )
    cli = importlib.import_module("ai_quant_os.strategy_execution_tape_cli")
    args = [
        "completions",
        "assemble",
        "--archive",
        str(archive),
        "--package-ref",
        package.package_ref,
        "--receipts",
        str(source),
        "--receipts-sha256",
        sha256(source.read_bytes()).hexdigest(),
        "--image-id",
        IMAGE,
        "--worker-sha256",
        WORKER,
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 0
    assert type(assemble(package, receipts)).model_validate_json(output.read_bytes()) == assemble(
        package, receipts
    )


def test_windows_path_alias_cannot_reuse_one_job_for_two_receipts(package, receipts):
    rows = [r.model_dump(mode="json") for r in receipts[:2]]
    name = rows[0]["ownership"]["job_name"]
    for i, row in enumerate(rows):
        row["ownership"]["job_name"] = name
        row["ownership"]["job_path"] = (
            ("D:\\metadata\\" + name) if i == 0 else ("d:/metadata/./" + name)
        )
        row["removal"]["ownership"] = row["ownership"]
    altered = tuple(StrategyPrefixExecutionReceipt.model_validate(row) for row in rows)
    with pytest.raises(ValueError):
        assemble(package, (*altered, *receipts[2:]))
