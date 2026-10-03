import json
import subprocess
import sys

from test_policy_rates import NOW, recipe


def test_installed_data_cli_ingests_lists_and_replays_checked_rate_capture(tmp_path):
    from ai_quant_os.data_cli import main

    source = tmp_path / "rates.csv"
    source.write_bytes(recipe())
    root = tmp_path / "archive"
    command = [sys.executable, "-m", "ai_quant_os.data_cli"]
    first = subprocess.run(
        command + ["ingest-ecb-rates", str(source), "--root", str(root), "--at", NOW.isoformat()],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert first.returncode == 0, first.stderr
    body = json.loads(first.stdout)
    assert body["capture"]["capture_mode"] == "LOCAL_CSV_IMPORT"
    ref = body["capture_ref"]
    listed = subprocess.run(
        command + ["list-rates", "--root", str(root)], capture_output=True, text=True, timeout=30
    )
    assert listed.returncode == 0 and json.loads(listed.stdout)[0]["capture_ref"] == ref
    replay = subprocess.run(
        command + ["replay-rates", ref, "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert replay.returncode == 0 and json.loads(replay.stdout) == body
    assert main(["replay-rates", "rate-capture:" + "0" * 64, "--root", str(root)]) == 2


def test_fetch_command_publishes_only_complete_verified_retrieval(tmp_path, monkeypatch, capsys):
    from ai_quant_os.data_cli import main
    from ai_quant_os.ecb_rates import EcbRateClient, RetrievedRates

    async def fetch(self):
        return RetrievedRates(recipe(), NOW)

    monkeypatch.setattr(EcbRateClient, "fetch", fetch)
    assert main(["fetch-ecb-rates", "--root", str(tmp_path)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["capture"]["capture_mode"] == "ECB_HTTPS_RETRIEVAL"
    assert body["capture"]["received_at"] == "2026-10-03T00:00:00Z"


def test_failed_fetch_does_not_create_archive_or_print_success(tmp_path, monkeypatch, capsys):
    from ai_quant_os.data_cli import main
    from ai_quant_os.ecb_rates import EcbRateClient

    async def fetch(self):
        raise ValueError("upstream unavailable")

    monkeypatch.setattr(EcbRateClient, "fetch", fetch)
    root = tmp_path / "absent"
    assert main(["fetch-ecb-rates", "--root", str(root)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "refused" in output.err
    assert not (root / "rates.sqlite3").exists()


def test_invalid_import_is_refused_before_archive_creation(tmp_path, capsys):
    from ai_quant_os.data_cli import main

    source = tmp_path / "bad.csv"
    source.write_bytes(b"x" * 65537)
    root = tmp_path / "absent"
    assert main(["ingest-ecb-rates", str(source), "--root", str(root)]) == 2
    assert capsys.readouterr().out == "" and not (root / "rates.sqlite3").exists()


def test_read_commands_cannot_initialize_missing_archive(tmp_path, capsys):
    from ai_quant_os.data_cli import main

    assert main(["list-rates", "--root", str(tmp_path)]) == 2
    assert not (tmp_path / "rates.sqlite3").exists() and capsys.readouterr().out == ""
