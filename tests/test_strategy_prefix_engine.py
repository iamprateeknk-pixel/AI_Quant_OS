"""Opt-in real engine check; proposed source runs only in the admitted worker."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from ai_quant_os.strategy_prefix_run import prepare_strategy_prefix_run, run_strategy_prefixes
from ai_quant_os.repair_sandbox import local_docker_command
from test_strategy_prefix_worker import package as package


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("AI_QUANT_STRATEGY_ENGINE_TEST") != "1",
    reason="Explicit local strategy-engine integration admission required",
)


def test_real_engine_full_prefix_run_and_restart(package, tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    manifest = prepare_strategy_prefix_run(
        package,
        root=root,
        archive=ROOT / "data-local/strategy-worker/oci-linux-cp311-01/image.oci.tar",
        base_metadata=ROOT / "infra/repair-worker",
        worker_sha256="c94e1dc2a34c31a76bd42413b16dc4b906cec123e107393156d61ef0f3cc09ee",
        base_manifest_digest="sha256:31feac62e3aa66d2cdddb4e1f80f839d8f14ad02b98b77e7eee90df6dc65f0c5",
        archive_sha256="d233bc9c8a2093d18c4392a913db5aaf18d3d27ae5838af58d339653e268f375",
        manifest_digest="sha256:1e0b8a64de1536ba581f7ffaead12574996f48e7385054c62f5a60826ef95e48",
        config_digest="sha256:415c3433e8ec2d3ac89463032e6bcad71e25f58b03632f1fe516b02c100b5cf7",
    )
    report = run_strategy_prefixes(package, root=root, run_sha256=manifest.run_sha256)
    receipts = report.replay.collection.receipts
    assert len(receipts) == package.holdout.receipt.row_count - 2
    assert len({row.exit.container_id for row in receipts}) == len(receipts)
    assert all(row.exit.exit_code == 0 for row in receipts)
    assert not [path for path in root.glob("strategy-job-*") if path.is_dir()]
    assert report.stress_replay.collection == report.replay.collection
    assert report.replay.replay.engine_version == "1.221.0"
    assert not report.execution_verified and not report.promotion_allowed
    tape = report.replay.collection.tape
    bars = tape.require_package(package).bars
    sign = (
        1 if package.candidate.review.hypothesis.rule == "FOLLOW_PREVIOUS_CLOSE_DIRECTION" else -1
    )
    expected = [0, 0] + [
        sign * ((bars[i - 1].close > bars[i - 2].close) - (bars[i - 1].close < bars[i - 2].close))
        for i in range(2, len(bars))
    ]
    assert [row.target for row in tape.decisions] == expected
    retained = (root / "strategy-run-replay.json").read_bytes()
    restarted = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_quant_os.strategy_prefix_run_cli",
            "--validation-archive",
            str(tmp_path / "paper.validation-packages.sqlite3"),
            "--package-ref",
            package.package_ref,
            "--root",
            str(root),
            "run",
            "--run-sha256",
            manifest.run_sha256,
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    assert json.loads(restarted.stdout)["report_ref"] == report.report_ref
    assert (root / "strategy-run-replay.json").read_bytes() == retained
    remaining = local_docker_command(
        shutil.which("docker"), "container", "ls", "--all", "--no-trunc", "--format", "{{.ID}}"
    )
    assert remaining.returncode == 0
    assert not {row.exit.container_id for row in receipts}.intersection(
        remaining.stdout.decode("ascii").splitlines()
    )
