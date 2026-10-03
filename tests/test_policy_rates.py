import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO

import pytest

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
HEADER = "KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF,TIME_FORMAT,UNIT,UNIT_MULT"


def recipe():
    return (
        HEADER
        + "\n"
        + "\n".join(
            f"FM.B.U2.EUR.4F.KR.{rate}.LEV,B,U2,EUR,4F,KR,{rate},LEV,{day},{value},A,F,P1D,PCPA,0"
            for rate in ("DFR", "MRR_FR", "MLFR")
            for day, value in [("2026-09-01", "-0.25"), ("2026-09-16", "2.5")]
        )
        + "\n"
    ).encode()


def test_complete_csv_preserves_rate_change_dates_values_units_and_raw_identity():
    from ai_quant_os.policy_rates import normalize_ecb_rates
    from hashlib import sha256

    capture = normalize_ecb_rates(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    assert capture.raw_sha256 == sha256(recipe()).hexdigest()
    assert len(capture.series) == 3
    assert all(row.unit == "PERCENT_PER_ANNUM" for row in capture.series)
    assert capture.series[0].observations[0].value == Decimal("-0.25")
    assert capture.series[0].observations[-1].period.isoformat() == "2026-09-16"
    assert capture.current_applicability == "UNASSESSED"
    assert capture.historical_vintage_guarantee is False


@pytest.mark.parametrize(
    "column,value",
    [
        ("KEY", "FM.B.U2.USD.4F.KR.DFR.LEV"),
        ("CURRENCY", "USD"),
        ("FREQ", "D"),
        ("PROVIDER_FM", "RT"),
        ("UNIT", "PC"),
        ("UNIT_MULT", "2"),
        ("OBS_STATUS", "M"),
        ("OBS_CONF", "C"),
        ("TIME_PERIOD", "2026-09"),
        ("OBS_VALUE", "NaN"),
        ("OBS_VALUE", "1e1000000"),
        ("OBS_VALUE", "0e-1000000"),
        ("OBS_VALUE", "2." + "0" * 25),
    ],
)
def test_wrong_dimensions_status_units_date_or_numeric_representation_are_refused(column, value):
    from ai_quant_os.policy_rates import normalize_ecb_rates

    rows = list(csv.reader(StringIO(recipe().decode())))
    rows[1][rows[0].index(column)] = value
    output = StringIO()
    csv.writer(output).writerows(rows)
    with pytest.raises(ValueError):
        normalize_ecb_rates(
            output.getvalue().encode(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT"
        )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_series",
        "duplicate_date",
        "third_observation",
        "duplicate_header",
        "extra_cell",
        "oversized",
        "invalid_utf8",
    ],
)
def test_incomplete_or_ambiguous_or_oversized_csv_never_becomes_a_capture(fault):
    from ai_quant_os.policy_rates import normalize_ecb_rates

    lines = recipe().decode().splitlines()
    if fault == "missing_series":
        lines = lines[:-2]
    elif fault == "duplicate_date":
        lines[2] = lines[1]
    elif fault == "third_observation":
        lines.append(lines[1].replace("2026-09-01", "2026-09-02"))
    elif fault == "duplicate_header":
        lines[0] += ",KEY"
    elif fault == "extra_cell":
        lines[1] += ",EXTRA"
    raw = "\n".join(lines).encode()
    if fault == "oversized":
        raw = b"x" * 65537
    elif fault == "invalid_utf8":
        raw = b"\xff"
    with pytest.raises(ValueError):
        normalize_ecb_rates(raw, received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")


def test_future_dated_observations_remain_dated_and_unassessed():
    from ai_quant_os.policy_rates import normalize_ecb_rates

    raw = recipe().replace(b"2026-09-16", b"2026-10-07")
    capture = normalize_ecb_rates(raw, received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    assert capture.series[0].observations[-1].period.isoformat() == "2026-10-07"
    assert capture.current_applicability == "UNASSESSED"


def test_capture_identity_normalizes_utc_and_ignores_csv_row_order_but_retains_raw_hash():
    from ai_quant_os.policy_rates import normalize_ecb_rates
    from datetime import timedelta

    offset = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    first = normalize_ecb_rates(recipe(), received_at=offset, capture_mode="LOCAL_CSV_IMPORT")
    same = normalize_ecb_rates(recipe(), received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    assert first.capture_ref == same.capture_ref
    lines = recipe().decode().splitlines()
    shuffled = ("\n".join([lines[0], *reversed(lines[1:])]) + "\n").encode()
    other = normalize_ecb_rates(shuffled, received_at=NOW, capture_mode="LOCAL_CSV_IMPORT")
    assert other.series == first.series and other.capture_ref != first.capture_ref
