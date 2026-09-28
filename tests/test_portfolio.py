from datetime import datetime, timezone
from decimal import Decimal

import pytest

from ai_quant_os.portfolio import PaperFill, PortfolioExposure, summarize_position


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fill(fill_id: str, side: str, quantity: str, price: str, fee: str = "0") -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        instrument_id="EURUSD",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal(fee),
        observed_at=NOW,
    )


def test_marked_pnl_uses_cash_flows_and_open_position() -> None:
    fills = (
        fill("buy", "BUY", "100", "1.10", "0.10"),
        fill("sell", "SELL", "40", "1.20", "0.10"),
    )
    position = summarize_position(fills, mark=Decimal("1.15"))
    assert position.net_quantity == Decimal("60")
    assert position.cash_flow_quote == Decimal("-62.20")
    assert position.gross_exposure_quote == Decimal("69.00")
    assert position.marked_pnl_quote == Decimal("6.80")


def test_short_position_uses_negative_quantity_and_positive_cash() -> None:
    position = summarize_position(
        (fill("short", "SELL", "10", "1.20", "0.10"),), mark=Decimal("1.10")
    )
    assert position.net_quantity == Decimal("-10")
    assert position.cash_flow_quote == Decimal("11.90")
    assert position.gross_exposure_quote == Decimal("11.00")
    assert position.marked_pnl_quote == Decimal("0.90")


def test_unmarked_position_reports_quantity_without_invented_pnl() -> None:
    position = summarize_position((fill("buy", "BUY", "10", "1.10"),), mark=None)
    assert position.net_quantity == Decimal("10")
    assert position.cash_flow_quote == Decimal("-11.00")
    assert position.gross_exposure_quote is None
    assert position.marked_pnl_quote is None


@pytest.mark.parametrize("field,value", [("quantity", "0"), ("price", "NaN"), ("fee", "-1")])
def test_invalid_fill_terms_are_rejected(field: str, value: str) -> None:
    values = dict(fill("bad", "BUY", "1", "1.1").__dict__)
    values[field] = Decimal(value)
    with pytest.raises(ValueError):
        PaperFill(**values)


def test_mixed_instruments_cannot_be_summed_without_fx_conversion() -> None:
    first = fill("one", "BUY", "1", "1.1")
    second = PaperFill(**{**fill("two", "BUY", "1", "1.1").__dict__, "instrument_id": "GBPUSD"})
    with pytest.raises(ValueError, match="one instrument"):
        summarize_position((first, second), mark=Decimal("1.1"))


def test_worst_case_exposure_includes_filled_position_and_pending_orders() -> None:
    snapshot = PortfolioExposure(
        snapshot_id="paper:test",
        instrument_id="EURUSD",
        filled_net_quantity=Decimal("800"),
        pending_buy_quantity=Decimal("200"),
        pending_sell_quantity=Decimal("300"),
    )
    assert snapshot.worst_case_quantity == Decimal("1000")
    assert snapshot.with_order("BUY", Decimal("600")) == Decimal("1600")
    assert snapshot.with_order("SELL", Decimal("400")) == Decimal("1000")


def test_invalid_pending_exposure_is_rejected() -> None:
    with pytest.raises(ValueError):
        PortfolioExposure(
            snapshot_id="paper:test",
            instrument_id="EURUSD",
            filled_net_quantity=Decimal("0"),
            pending_buy_quantity=Decimal("-1"),
            pending_sell_quantity=Decimal("0"),
        )
