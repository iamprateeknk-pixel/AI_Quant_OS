from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json

import pytest

from ai_quant_os.candidate_validation_store import CandidateValidationStore
from ai_quant_os.holdout_validator_service import HoldoutRequestHandler
from ai_quant_os.research_evidence_catalog import HoldoutEvidenceStore, ResearchEvidenceCatalog
from ai_quant_os.validation_package_store import ValidationPackageStore
from test_research_evidence_catalog import NOW, raw_bars


def setup_archives(tmp_path):
    training_path = tmp_path / "paper.research-data.sqlite3"
    with ResearchEvidenceCatalog(training_path, training_only=True) as training:
        training.publish(
            raw_bars(),
            source_id="training",
            resource_class="TRAINING",
            received_at=NOW,
        )
    service_source = tmp_path / "service-research.sqlite3"
    with ResearchEvidenceCatalog(service_source):
        pass
    holdout_path = service_source.with_name("service-research.holdout.sqlite3")
    return training_path, holdout_path


def test_holdout_cli_publishes_pinned_csv_via_service_only(tmp_path):
    from ai_quant_os.holdout_data_cli import main
    from ai_quant_os.holdout_validator_rpc import verify_response

    training_path, holdout_path = setup_archives(tmp_path)
    source = tmp_path / "holdout.csv"
    raw = raw_bars(minute=10)
    source.write_bytes(raw)
    with (
        HoldoutEvidenceStore(holdout_path) as holdouts,
        ValidationPackageStore(tmp_path / "packages.sqlite3") as packages,
        CandidateValidationStore(tmp_path / "validations.sqlite3") as validations,
    ):
        handler = HoldoutRequestHandler(holdouts, packages, validations, research=None)

        class Client:
            def call(self, request):
                return verify_response(request, handler(request))

        output = StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "publish",
                    "--training-archive",
                    str(training_path),
                    "--input",
                    str(source),
                    "--sha256",
                    sha256(raw).hexdigest(),
                    "--source-id",
                    "sealed-feed",
                ],
                holdout_client=Client(),
            )
        assert result == 0
        published = json.loads(output.getvalue())
        assert published["receipt"]["resource_class"] == "HOLDOUT"
        assert published["isolation_verified"] is False
        assert published["execution_authorized"] is False
        assert raw.decode() not in output.getvalue()
        assert holdouts.get(published["receipt"]["dataset_ref"])[1] == raw


def test_holdout_cli_refuses_bad_pin_and_overlapping_training_input(tmp_path):
    from ai_quant_os.holdout_data_cli import main

    training_path, holdout_path = setup_archives(tmp_path)
    source = tmp_path / "holdout.csv"
    raw = raw_bars()
    source.write_bytes(raw)
    with (
        HoldoutEvidenceStore(holdout_path) as holdouts,
        ValidationPackageStore(tmp_path / "packages.sqlite3") as packages,
        CandidateValidationStore(tmp_path / "validations.sqlite3") as validations,
    ):
        handler = HoldoutRequestHandler(holdouts, packages, validations, research=None)

        class Client:
            calls = 0

            def call(self, request):
                self.calls += 1
                return handler(request)

        client = Client()
        args = [
            "publish",
            "--training-archive",
            str(training_path),
            "--input",
            str(source),
            "--sha256",
            "0" * 64,
            "--source-id",
            "sealed-feed",
        ]
        with pytest.raises(SystemExit) as error:
            main(args, holdout_client=client)
        assert error.value.code == 2 and client.calls == 0

        args[args.index("0" * 64)] = sha256(raw).hexdigest()
        with pytest.raises(SystemExit) as error:
            main(args, holdout_client=client)
        assert error.value.code == 2 and client.calls == 1
        assert holdouts.list() == ()
