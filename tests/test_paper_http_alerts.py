"""Native Prometheus evaluates production alert expressions and hold/recovery timing."""

from pathlib import Path
import json
import shutil
import subprocess

from tests.test_prometheus_deployment import native_tools  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "infra/observability/paper-http-alerts.json"


def test_native_promtool_alert_hold_recovery_missing_and_other_jobs(request, tmp_path):
    tools = request.getfixturevalue("native_tools")
    runbook = ROOT / "docs/runbooks/paper-http-monitoring.md"
    assert "## Scrape failed" in runbook.read_text() and "## Scrape missing" in runbook.read_text()
    shutil.copyfile(RULES, tmp_path / RULES.name)
    failed = "PaperHttpScrapeFailed"
    missing = "PaperHttpScrapeMissing"
    labels = {"job": "paper_http", "instance": "127.0.0.1:8000", "severity": "warning"}
    annotations = {
        "summary": "Paper HTTP metrics scrape failed",
        "runbook_path": "docs/runbooks/paper-http-monitoring.md#scrape-failed",
    }
    missing_annotations = {
        "summary": "Paper HTTP metrics scrape observation missing",
        "runbook_path": "docs/runbooks/paper-http-monitoring.md#scrape-missing",
    }

    def expectation(name, when, alerts):
        return {"alertname": name, "eval_time": when, "exp_alerts": alerts}

    firing = [{"exp_labels": labels, "exp_annotations": annotations}]
    groups = [
        {
            "name": "healthy and brief failure recover without firing",
            "input_series": [
                {
                    "series": 'up{job="paper_http",instance="127.0.0.1:8000"}',
                    "values": "1 0 0 0 1 1 1 1 1 1 1 1 1",
                },
            ],
            "alert_rule_test": [
                expectation(name, when, [])
                for name in (failed, missing)
                for when in ("0s", "45s", "3m")
            ],
        },
        {
            "name": "sustained failure exact hold then recovery",
            "input_series": [
                {
                    "series": 'up{job="paper_http",instance="127.0.0.1:8000"}',
                    "values": "0+0x8 1+0x4",
                },
            ],
            "alert_rule_test": [
                expectation(failed, "105s", []),
                expectation(failed, "2m", firing),
                expectation(failed, "135s", []),
                expectation(missing, "2m", []),
            ],
        },
        {
            "name": "missing source including unrelated healthy job",
            "input_series": [{"series": 'up{job="other"}', "values": "1+0x12"}],
            "alert_rule_test": [
                expectation(missing, "105s", []),
                expectation(
                    missing,
                    "2m",
                    [
                        {
                            "exp_labels": {"job": "paper_http", "severity": "warning"},
                            "exp_annotations": missing_annotations,
                        }
                    ],
                ),
                expectation(failed, "3m", []),
            ],
        },
        {
            "name": "stale marker withdraws original source and recovers",
            "input_series": [
                {
                    "series": 'up{job="paper_http",instance="127.0.0.1:8000"}',
                    "values": "1 stale _ _ _ _ _ _ _ _ 1 1 1",
                },
            ],
            "alert_rule_test": [
                expectation(missing, "2m", []),
                expectation(
                    missing,
                    "135s",
                    [
                        {
                            "exp_labels": {"job": "paper_http", "severity": "warning"},
                            "exp_annotations": missing_annotations,
                        }
                    ],
                ),
                expectation(missing, "150s", []),
                expectation(failed, "135s", []),
            ],
        },
    ]
    for group in groups:
        group["interval"] = "15s"
    cases = {"rule_files": [RULES.name], "evaluation_interval": "15s", "tests": groups}
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    result = subprocess.run(
        [str(tools / "promtool.exe"), "test", "rules", "cases.json"],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    (tmp_path / "native-rule-test.log").write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(
        errors="replace"
    )
