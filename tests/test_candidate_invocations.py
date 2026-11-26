import base64
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal

import pytest

from ai_quant_os.market_data import BarDataset
from ai_quant_os.research_data_parsing import parse_research_csv
from test_signal_tape_replay import package as package_fixture


@pytest.fixture
def package(tmp_path):
    return package_fixture.__wrapped__(tmp_path)


def boundary():
    assert importlib.util.find_spec("ai_quant_os.candidate_invocations") is not None, (
        "Prefix-only candidate invocation boundary is required"
    )
    from ai_quant_os.candidate_invocations import prepare_invocation, assemble_unattested_tape
    from ai_quant_os.candidate_invocation_contracts import (
        CandidateInvocation,
        CandidateTargetOutput,
    )

    return prepare_invocation, assemble_unattested_tape, CandidateInvocation, CandidateTargetOutput


def outputs_for(package):
    prepare, _, _, output = boundary()
    rows = package.holdout.receipt.row_count
    return tuple(
        output(invocation_ref=prepare(package, i).invocation_ref, target=1) for i in range(2, rows)
    )


def test_each_invocation_contains_exact_two_closed_bars_and_no_full_dataset_or_review(package):
    prepare, _, _, _ = boundary()
    dataset = parse_research_csv(base64.b64decode(package.holdout.raw_base64))
    for i in range(2, len(dataset.bars)):
        request = prepare(package, i)
        assert request.package_ref == package.package_ref
        assert request.candidate_ref == package.candidate.candidate_ref
        assert request.source == package.candidate.files[0]
        assert request.sdk_files == package.sdk_files
        assert request.index == i and request.execute_at == dataset.bars[i].observed_at
        assert tuple(row.to_bar() for row in request.history) == dataset.bars[i - 2 : i]
        assert request.history_sha256 == BarDataset(dataset.bars[i - 2 : i]).fingerprint
        wire = json.loads(request.model_dump_json())
        assert set(wire) == {
            "schema_version",
            "package_ref",
            "candidate_ref",
            "index",
            "execute_at",
            "instrument_id",
            "source",
            "sdk_files",
            "history",
            "history_sha256",
            "status",
            "isolation_verified",
            "promotion_authorized",
        }
        assert len(request.model_dump_json().encode()) <= 524288
        assert not request.isolation_verified and not request.promotion_authorized
        assert "test_candidate.py" not in request.model_dump_json()
        for other in ("training", "holdout", "graph", "review", "raw_base64", "plan"):
            assert other not in wire


@pytest.mark.parametrize("index", [True, 0, 1, -1, 4096, "2"])
def test_warmup_missing_and_non_integer_invocations_are_refused(package, index):
    prepare, _, _, _ = boundary()
    with pytest.raises(ValueError):
        prepare(package, index)


@pytest.mark.parametrize(
    "change",
    [
        "future",
        "reversed",
        "wrong_instrument",
        "fingerprint",
        "source",
        "sdk",
        "extra",
        "authority",
    ],
)
def test_unbound_or_overprivileged_request_wire_is_refused(package, change):
    prepare, _, model, _ = boundary()
    payload = json.loads(prepare(package, 2).model_dump_json())
    if change == "future":
        payload["history"][-1]["observed_at"] = payload["execute_at"]
    elif change == "reversed":
        payload["history"].reverse()
    elif change == "wrong_instrument":
        payload["history"][-1]["instrument_id"] = "USDJPY"
    elif change == "fingerprint":
        payload["history_sha256"] = "0" * 64
    elif change == "source":
        payload["source"]["path"] = "test_candidate.py"
    elif change == "sdk":
        payload["sdk_files"].reverse()
    elif change == "extra":
        payload["holdout"] = "future rows"
    else:
        payload["isolation_verified"] = True
    with pytest.raises(ValueError):
        model.model_validate_json(json.dumps(payload))


def test_package_reconstruction_rejects_forged_model_copy_without_executing_source(package):
    prepare, _, _, _ = boundary()
    candidate = package.candidate.model_copy(update={"execution_authorized": True})
    forged = package.model_copy(update={"candidate": candidate})
    with pytest.raises(ValueError):
        prepare(forged, 2)


def test_untrusted_targets_assemble_in_open_order_with_fixed_warmup_and_no_attestation(package):
    _, assemble, _, _ = boundary()
    tape = assemble(package, tuple(reversed(outputs_for(package))))
    assert [row.target for row in tape.decisions] == [0, 0] + [1] * (
        package.holdout.receipt.row_count - 2
    )
    assert tape.provenance == "OPERATOR_SUPPLIED_UNATTESTED_TARGETS"
    assert not tape.isolation_verified and not tape.promotion_authorized
    tape.require_package(package)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "foreign", "bool", "outside", "extra", "authority"]
)
def test_output_inventory_and_authority_are_checked_before_tape_assembly(package, change):
    _, assemble, _, model = boundary()
    outputs = list(outputs_for(package))
    if change == "missing":
        outputs.pop()
    elif change == "duplicate":
        outputs[-1] = outputs[0]
    elif change == "foreign":
        outputs[0] = outputs[0].model_copy(
            update={"invocation_ref": "candidate-invocation:" + "0" * 64}
        )
    else:
        payload = json.loads(outputs[0].model_dump_json())
        if change == "bool":
            payload["target"] = True
        elif change == "outside":
            payload["target"] = 2
        elif change == "extra":
            payload["execution_authorized"] = True
        else:
            payload["isolation_verified"] = True
        with pytest.raises(ValueError):
            model.model_validate_json(json.dumps(payload))
        return
    with pytest.raises(ValueError):
        assemble(package, outputs)


def test_exact_parent_binding_rejects_self_consistent_changed_closed_prices(package):
    prepare, _, model, _ = boundary()
    request = prepare(package, 2)
    payload = json.loads(request.model_dump_json())
    for row in payload["history"]:
        for field in ("open", "high", "low", "close"):
            row[field] = str(Decimal(row[field]) + Decimal("1"))
    from ai_quant_os.candidate_invocation_contracts import ClosedBar

    history = tuple(ClosedBar.model_validate(row).to_bar() for row in payload["history"])
    payload["history_sha256"] = BarDataset(history).fingerprint
    forged = model.model_validate_json(json.dumps(payload))
    with pytest.raises(ValueError):
        forged.require_package(package)


def test_candidate_text_is_inert_during_request_preparation_and_target_assembly(
    package, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    prepare, assemble, _, _ = boundary()
    prepare(package, 2)
    assemble(package, outputs_for(package))
    assert not list(tmp_path.rglob("*.py"))


@pytest.mark.parametrize("value", [None, {}, "not a target"])
def test_non_model_target_rows_are_refused_with_validation_error(package, value):
    _, assemble, _, _ = boundary()
    outputs = list(outputs_for(package))
    outputs[0] = value
    with pytest.raises(ValueError):
        assemble(package, outputs)


def test_bounded_output_wire_refuses_oversized_and_noncanonical_json(package):
    _, _, _, model = boundary()
    assert hasattr(model, "from_wire"), "Bounded target decoder is required"
    row = outputs_for(package)[0]
    assert model.from_wire(row.model_dump_json().encode()) == row
    for wire in (
        b" " * 2049,
        row.model_dump_json().replace('"target":1', '"target":-1,"target":1').encode(),
        row.model_dump_json().replace('"target":1', '"target":NaN').encode(),
        b"",
        b"[]",
    ):
        with pytest.raises(ValueError):
            model.from_wire(wire)


def test_request_wire_decoder_enforces_bound_unique_keys_and_exact_roundtrip(package):
    prepare, _, model, _ = boundary()
    request = prepare(package, 2)
    assert model.from_wire(request.model_dump_json().encode()) == request
    for raw in (
        b" " * 524289,
        request.model_dump_json().replace('"index":2', '"index":3,"index":2').encode(),
        b"[" * 2000 + b"]" * 2000,
    ):
        with pytest.raises(ValueError):
            model.from_wire(raw)


def test_real_cli_prepares_one_prefix_json_and_assembles_exact_unattested_tape(package, tmp_path):
    prepare, _, model, _ = boundary()
    destination = tmp_path / "request.json"
    base = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_invocation_cli",
        "--archive",
        str(tmp_path / "service.validation-packages.sqlite3"),
        "--package-ref",
        package.package_ref,
    ]
    completed = subprocess.run(
        [*base, "prepare", "--index", "2", "--output", str(destination)],
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert model.from_wire(destination.read_bytes()) == prepare(package, 2)
    assert "source" not in json.loads(completed.stdout)
    again = subprocess.run(
        [*base, "prepare", "--index", "2", "--output", str(destination)],
        capture_output=True,
        timeout=30,
    )
    assert again.returncode == 2
    assert model.from_wire(destination.read_bytes()) == prepare(package, 2)
    response_paths = []
    for i, row in enumerate(outputs_for(package)):
        path = tmp_path / f"target-{i}.json"
        path.write_text(row.model_dump_json())
        response_paths.extend(["--response", str(path)])
    tape_path = tmp_path / "tape.json"
    completed = subprocess.run(
        [*base, "assemble", *response_paths, "--output", str(tape_path)],
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    from ai_quant_os.signal_tape_contracts import SignalTape

    tape = SignalTape.model_validate_json(tape_path.read_bytes())
    tape.require_package(package)
    assert json.loads(completed.stdout)["status"] == "UNATTESTED"
    assert not list(tmp_path.rglob("*.py"))


@pytest.mark.parametrize(
    "change",
    ["missing_archive", "corrupt_archive", "warmup", "oversized_response", "incomplete_response"],
)
def test_cli_refuses_invalid_archive_or_response_without_new_output(package, tmp_path, change):
    archive = tmp_path / "service.validation-packages.sqlite3"
    destination = tmp_path / "denied.json"
    command = [
        sys.executable,
        "-m",
        "ai_quant_os.candidate_invocation_cli",
        "--archive",
        str(archive),
        "--package-ref",
        package.package_ref,
    ]
    if change == "missing_archive":
        archive = tmp_path / "missing.sqlite3"
        command[4] = str(archive)
    elif change == "corrupt_archive":
        archive.write_bytes(b"not a SQLite archive")
    if change in ("oversized_response", "incomplete_response"):
        path = tmp_path / "response.json"
        path.write_bytes(
            b" " * 2049
            if change == "oversized_response"
            else outputs_for(package)[0].model_dump_json().encode()
        )
        command.extend(["assemble", "--response", str(path)])
    else:
        command.extend(["prepare", "--index", "1" if change == "warmup" else "2"])
    completed = subprocess.run(
        [*command, "--output", str(destination)], capture_output=True, timeout=30
    )
    assert completed.returncode == 2, completed.stderr
    assert not destination.exists()
    if change == "missing_archive":
        assert not archive.exists()
