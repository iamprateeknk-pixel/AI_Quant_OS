"""Research routes keep the base paper API usable without native dependencies."""

import subprocess
import sys


def test_native_dependency_is_loaded_only_for_enabled_research_requests(tmp_path):
    program = """
import importlib.abc
import sys
from pathlib import Path
class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'nautilus_trader':
            raise ImportError('Native package deliberately unavailable')
sys.meta_path.insert(0, NoNative())
from ai_quant_os.api import create_app
from fastapi.testclient import TestClient
headers = {'x-operator-token': 'test-secret'}
disabled = TestClient(create_app(operator_token='test-secret'))
assert disabled.get('/api/v1/research/candidates', headers=headers).json()['detail'] == 'Research workspace is disabled'
assert disabled.get('/api/v1/research/strategies/page', headers=headers).status_code == 503
enabled = TestClient(create_app(operator_token='test-secret', research_workspace=Path(sys.argv[1])))
response = enabled.get('/api/v1/research/candidates', headers=headers)
assert response.status_code == 503
assert response.json()['detail'] == 'Research registry is unavailable'
(Path(sys.argv[1]) / 'strategies').mkdir()
response = enabled.get('/api/v1/research/strategies/page', headers=headers)
assert response.status_code == 200 and response.json()['strategies'] == []
from ai_quant_os.strategy_registry import StrategyRegistry, prepare_builtin
from ai_quant_os.strategy_contracts import StrategyGenome
prepare_builtin(StrategyGenome('buy-and-hold@1.0.0', 'BAR'), StrategyRegistry(Path(sys.argv[1]) / 'strategies'))
response = enabled.get('/api/v1/research/strategies/page', headers=headers)
assert response.status_code == 200
assert response.json()['strategies'][0]['strategy_ref'] == 'buy-and-hold@1.0.0'
assert 'nautilus_trader' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
