"""Frontend fixture retains the actual original protected native API observation bytes."""

from hashlib import sha256
import json
from pathlib import Path

from ai_quant_os.monitoring_alerts import MonitoringObservation


def test_original_native_monitoring_response_fixture_identity_and_contract():
    raw = (
        Path(__file__).resolve().parents[1] / "apps/web/tests/fixtures/monitoring-alerts.json"
    ).read_bytes()
    assert (
        sha256(raw).hexdigest()
        == "8d7e90fe3692b80a9e02214d94130edd7280d9c934c40691643d03ae0a488e33"
    )
    value = json.loads(raw)
    observation = MonitoringObservation.model_validate(value)
    assert observation.state == "AVAILABLE"
    assert tuple(rule.state for rule in observation.rules) == ("PENDING", "INACTIVE")
    assert observation.model_dump(mode="json") == value
