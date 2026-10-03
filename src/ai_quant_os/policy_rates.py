"""Strict dated ECB rate facts; retrieval never establishes current applicability."""

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

ECB_RATE_URL = "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR+MRR_FR+MLFR.LEV?format=csvdata&lastNObservations=2"
MAX_RATE_CSV_BYTES = 65536
RateId = Literal["DFR", "MRR_FR", "MLFR"]
CaptureMode = Literal["ECB_HTTPS_RETRIEVAL", "LOCAL_CSV_IMPORT"]
RATE_IDS = frozenset({"DFR", "MRR_FR", "MLFR"})


class PolicyRateObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    period: date
    value: Decimal = Field(ge=-100, le=100, allow_inf_nan=False)
    observation_status: Literal["A"] = "A"

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: Decimal) -> Decimal:
        if len(value.as_tuple().digits) > 20 or not -9 <= value.as_tuple().exponent <= 9:
            raise ValueError("Rate representation exceeds budget")
        return value


class PolicyRateSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rate_id: RateId
    series_key: str
    currency: Literal["EUR"] = "EUR"
    unit: Literal["PERCENT_PER_ANNUM"] = "PERCENT_PER_ANNUM"
    observations: tuple[PolicyRateObservation, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def ordered_identity(self) -> "PolicyRateSeries":
        periods = tuple(row.period for row in self.observations)
        if (
            self.series_key != f"FM.B.U2.EUR.4F.KR.{self.rate_id}.LEV"
            or tuple(sorted(set(periods))) != periods
        ):
            raise ValueError("Rate series identity or date order mismatch")
        return self


class RateCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    adapter_version: Literal["ECB_FM_POLICY_RATES_V1"] = "ECB_FM_POLICY_RATES_V1"
    source_url: Literal[ECB_RATE_URL] = ECB_RATE_URL
    capture_mode: CaptureMode
    received_at: AwareDatetime
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    date_semantics: Literal["DATE_OF_RATE_CHANGE"] = "DATE_OF_RATE_CHANGE"
    current_applicability: Literal["UNASSESSED"] = "UNASSESSED"
    historical_vintage_guarantee: Literal[False] = False
    series: tuple[PolicyRateSeries, ...] = Field(min_length=3, max_length=3)

    @field_validator("received_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(timezone.utc)
        except OverflowError as error:
            raise ValueError("Rate retrieval timestamp outside UTC range") from error

    @model_validator(mode="after")
    def complete_series(self) -> "RateCapture":
        if tuple(row.rate_id for row in self.series) != tuple(sorted(RATE_IDS)):
            raise ValueError("Missing or unordered required ECB rate series")
        return self

    @property
    def capture_ref(self) -> str:
        return "rate-capture:" + sha256(self.model_dump_json().encode()).hexdigest()


def normalize_ecb_rates(
    raw: bytes, *, received_at: datetime, capture_mode: CaptureMode
) -> RateCapture:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RATE_CSV_BYTES:
        raise ValueError("Rate CSV exceeds byte budget")
    try:
        reader = csv.reader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader)
        required = {
            "KEY",
            "FREQ",
            "REF_AREA",
            "CURRENCY",
            "PROVIDER_FM",
            "INSTRUMENT_FM",
            "PROVIDER_FM_ID",
            "DATA_TYPE_FM",
            "TIME_PERIOD",
            "OBS_VALUE",
            "OBS_STATUS",
            "OBS_CONF",
            "TIME_FORMAT",
            "UNIT",
            "UNIT_MULT",
        }
        if (
            not required <= set(header)
            or len(set(header)) != len(header)
            or not len(header) <= 80
            or any(not value for value in header)
        ):
            raise ValueError("Ambiguous or missing rate CSV columns")
        grouped = {key: [] for key in RATE_IDS}
        for cells in reader:
            if not cells:
                continue
            if len(cells) != len(header) or any(len(value) > 2048 for value in cells):
                raise ValueError("Invalid rate CSV row")
            row = dict(zip(header, cells, strict=True))
            rate = row["PROVIDER_FM_ID"]
            expected = {
                "KEY": f"FM.B.U2.EUR.4F.KR.{rate}.LEV",
                "FREQ": "B",
                "REF_AREA": "U2",
                "CURRENCY": "EUR",
                "PROVIDER_FM": "4F",
                "INSTRUMENT_FM": "KR",
                "DATA_TYPE_FM": "LEV",
                "OBS_STATUS": "A",
                "OBS_CONF": "F",
                "TIME_FORMAT": "P1D",
                "UNIT": "PCPA",
                "UNIT_MULT": "0",
            }
            if (
                rate not in RATE_IDS
                or any(row[key] != value for key, value in expected.items())
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["TIME_PERIOD"]) is None
                or len(row["OBS_VALUE"]) > 64
            ):
                raise ValueError("Rate source dimensions or status mismatch")
            grouped[rate].append(
                PolicyRateObservation(
                    period=date.fromisoformat(row["TIME_PERIOD"]), value=row["OBS_VALUE"]
                )
            )
            if len(grouped[rate]) > 2:
                raise ValueError("Too many rate observations")
        series = tuple(
            PolicyRateSeries(
                rate_id=key,
                series_key=f"FM.B.U2.EUR.4F.KR.{key}.LEV",
                observations=tuple(sorted(grouped[key], key=lambda row: row.period)),
            )
            for key in sorted(RATE_IDS)
        )
        return RateCapture(
            capture_mode=capture_mode,
            received_at=received_at,
            raw_sha256=sha256(raw).hexdigest(),
            series=series,
        )
    except (UnicodeError, csv.Error, StopIteration) as error:
        raise ValueError("Malformed rate CSV") from error
