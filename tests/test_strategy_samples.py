from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util

import pytest

from ai_quant_os.market_data import Bar


def history():
    return tuple(
        Bar(
            "EURUSD",
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(1),
        )
        for i, price in enumerate(("1.1", "1.2", "1.3"))
    )


def test_sample_plugins_are_deterministic_and_wait_for_complete_windows():
    assert importlib.util.find_spec("ai_quant_os.strategy_samples") is not None
    from ai_quant_os.strategy_samples import BuyAndHold, MovingAverageCrossover

    assert BuyAndHold().target_position(()) == 0
    assert BuyAndHold().target_position(history()) == 1
    strategy = MovingAverageCrossover(2, 3)
    assert strategy.target_position(history()[:2]) == 0
    assert strategy.target_position(history()) == 1
    assert strategy.target_position(history()) == strategy.target_position(history())
    with pytest.raises(ValueError):
        MovingAverageCrossover(3, 2)
