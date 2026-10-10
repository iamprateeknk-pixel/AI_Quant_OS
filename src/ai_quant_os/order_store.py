import json
import sqlite3
from hashlib import sha256
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock

from ai_quant_os.portfolio import PaperFill, PortfolioExposure
from ai_quant_os.currency_portfolio import CurrencyPendingOrder
from ai_quant_os.currency_risk import CurrencyReservation
from ai_quant_os.execution_attribution import DecisionPriceBenchmark, encode_benchmark
from ai_quant_os.market import MarketSnapshot


def _encode_fill(fill: PaperFill) -> str:
    return json.dumps(
        asdict(fill),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        sort_keys=True,
    )


def _decode_fill(raw: str) -> PaperFill:
    data = json.loads(raw)
    for field in ("quantity", "price", "fee"):
        data[field] = Decimal(data[field])
    data["observed_at"] = datetime.fromisoformat(data["observed_at"])
    return PaperFill(**data)


class PaperOrderStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connection = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False, timeout=30
        )
        self._lock = Lock()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_orders (
                    idempotency_key TEXT PRIMARY KEY,
                    intent_json TEXT NOT NULL,
                    order_json TEXT NOT NULL,
                    intent_id TEXT UNIQUE,
                    order_id TEXT UNIQUE
                )"""
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(paper_orders)")
            }
            for column in ("intent_id", "order_id"):
                if column not in columns:
                    self._connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} TEXT")
            rows = self._connection.execute(
                "SELECT idempotency_key, order_json, intent_id, order_id FROM paper_orders"
            ).fetchall()
            for key, raw, intent_id, order_id in rows:
                order = json.loads(raw)
                if (intent_id is not None and intent_id != order["intent_id"]) or (
                    order_id is not None and order_id != order["order_id"]
                ):
                    raise ValueError("Paper order index disagrees with receipt")
                if intent_id is None or order_id is None:
                    self._connection.execute(
                        "UPDATE paper_orders SET intent_id = ?, order_id = ? WHERE idempotency_key = ?",
                        (order["intent_id"], order["order_id"], key),
                    )
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_intent_id ON paper_orders(intent_id)"
            )
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_order_id ON paper_orders(order_id)"
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS paper_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    fill_json TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_fills_order_id ON paper_fills(order_id)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS paper_price_benchmarks (order_id TEXT PRIMARY KEY, benchmark_json TEXT NOT NULL, sha256 TEXT NOT NULL)"
            )
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(paper_orders)")
            }
            if "benchmark_required" not in columns:
                self._connection.execute(
                    "ALTER TABLE paper_orders ADD COLUMN benchmark_required INTEGER NOT NULL DEFAULT 0"
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def get(self, key: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT intent_json, order_json FROM paper_orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return (row[0], row[1]) if row else None

    def order_count(self) -> int:
        """Observe inventory size without loading financial records."""
        with self._lock:
            return self._connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]

    def all_orders(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT order_json FROM paper_orders ORDER BY rowid"
            ).fetchall()
            return tuple(row[0] for row in rows)

    def get_by_intent_id(self, intent_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT order_json FROM paper_orders WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return row[0] if row else None

    def get_order_by_id(self, order_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT order_json FROM paper_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return row[0] if row else None

    def all_fills(self) -> tuple[PaperFill, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT fill_json FROM paper_fills ORDER BY rowid"
            ).fetchall()
            return tuple(_decode_fill(row[0]) for row in rows)

    def currency_ledger(self) -> tuple[tuple[PaperFill, ...], tuple[CurrencyPendingOrder, ...]]:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                result = self._currency_ledger_unlocked()
                self._connection.commit()
                return result
            except BaseException:
                self._connection.rollback()
                raise

    def attribution_records(self):
        rows, fills, _ = self.execution_evidence_records()
        return rows, fills

    def execution_evidence_records(self):
        """Read persisted ownership and fills in one bounded database snapshot."""
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                rows = self._connection.execute(
                    "SELECT order_id,intent_id,intent_json,order_json FROM paper_orders ORDER BY order_id LIMIT 10001"
                ).fetchall()
                fill_rows = self._connection.execute(
                    "SELECT fill_id,fill_json FROM paper_fills ORDER BY fill_id LIMIT 10001"
                ).fetchall()
                if len(rows) > 10000 or len(fill_rows) > 10000:
                    raise ValueError("Paper attribution ledger capacity exceeded")
                fills = tuple(_decode_fill(row[1]) for row in fill_rows)
                if any(row[0] != fill.fill_id for row, fill in zip(fill_rows, fills, strict=True)):
                    raise ValueError("Paper attribution fill storage identity mismatch")
                benchmark_rows = self._connection.execute(
                    "SELECT order_id,benchmark_json,sha256 FROM paper_price_benchmarks ORDER BY order_id LIMIT 10001"
                ).fetchall()
                if len(benchmark_rows) > 10000:
                    raise ValueError("Paper benchmark inventory capacity exceeded")
                benchmarks = tuple(self._decode_benchmark(*row) for row in benchmark_rows)
                present = {row.order_id for row in benchmarks}
                order_ids = {row[0] for row in rows}
                metadata = self._connection.execute(
                    "SELECT order_id,benchmark_required FROM paper_orders"
                ).fetchall()
                if any(type(flag) is not int or flag not in (0, 1) for _, flag in metadata):
                    raise ValueError("Invalid paper benchmark requirement marker")
                required = {identifier for identifier, flag in metadata if flag == 1}
                if present - order_ids or present != required:
                    raise ValueError("Paper benchmark inventory lineage mismatch")
                self._connection.commit()
                return tuple(rows), fills, benchmarks
            except BaseException:
                self._connection.rollback()
                raise

    @staticmethod
    def _decode_benchmark(order_id, raw, digest):
        if (
            not isinstance(raw, str)
            or len(raw.encode()) > 65536
            or sha256(raw.encode()).hexdigest() != digest
        ):
            raise ValueError("Paper benchmark fingerprint mismatch")
        try:
            record = json.loads(raw)
            market = record.pop("market")
            market["bid"], market["ask"] = Decimal(market["bid"]), Decimal(market["ask"])
            market["observed_at"] = datetime.fromisoformat(market["observed_at"])
            record["decision_at"] = datetime.fromisoformat(record["decision_at"])
            benchmark = DecisionPriceBenchmark(**record, market=MarketSnapshot(**market))
        except (TypeError, KeyError, AttributeError) as error:
            raise ValueError("Invalid paper benchmark record") from error
        if benchmark.order_id != order_id or encode_benchmark(benchmark).decode() != raw:
            raise ValueError("Paper benchmark identity or canonical encoding mismatch")
        return benchmark

    def _record_benchmark_unlocked(self, benchmark, order_json, intent_json):
        order, intent = json.loads(order_json), json.loads(intent_json)
        if (
            benchmark.order_id != order["order_id"]
            or benchmark.risk_id != order["risk_id"]
            or benchmark.intent_id != order["intent_id"]
            or benchmark.intent_id != intent["intent_id"]
            or benchmark.market.instrument_id != intent["instrument_id"]
            or benchmark.market.world_state_version != intent["world_state_version"]
        ):
            raise ValueError("Initial paper benchmark ownership mismatch")
        raw = encode_benchmark(benchmark)
        if len(raw) > 65536:
            raise ValueError("Paper benchmark record capacity exceeded")
        self._connection.execute(
            "INSERT INTO paper_price_benchmarks VALUES (?,?,?)",
            (benchmark.order_id, raw.decode(), sha256(raw).hexdigest()),
        )

    def _currency_ledger_unlocked(
        self,
    ) -> tuple[tuple[PaperFill, ...], tuple[CurrencyPendingOrder, ...]]:
        order_rows = self._connection.execute(
            "SELECT order_id, order_json, intent_json FROM paper_orders ORDER BY order_id"
        ).fetchall()
        orders = {row[0]: json.loads(row[1]) for row in order_rows}
        slippage = {row[0]: Decimal(json.loads(row[2])["max_slippage"]) for row in order_rows}
        fills = tuple(
            _decode_fill(row[0])
            for row in self._connection.execute(
                "SELECT fill_json FROM paper_fills ORDER BY fill_id"
            )
        )
        quantities: dict[str, Decimal] = {}
        for fill in fills:
            order = orders.get(fill.order_id)
            if order is None or order["status"] != "ACCEPTED":
                raise ValueError("Currency ledger fill has no accepted order")
            command = order["command"]
            if fill.instrument_id != command["instrument_id"] or fill.side != command["side"]:
                raise ValueError("Currency ledger fill does not match order")
            quantities[fill.order_id] = quantities.get(fill.order_id, Decimal(0)) + fill.quantity
        pending: list[CurrencyPendingOrder] = []
        for order_id, order in orders.items():
            if order["status"] not in {
                "PENDING",
                "DISPATCHING",
                "UNKNOWN",
                "ACCEPTED",
                "REJECTED",
            }:
                raise ValueError("Unknown currency ledger order state")
            if order["status"] == "REJECTED":
                continue
            command = order["command"]
            remaining = Decimal(command["quantity"]) - quantities.get(order_id, Decimal(0))
            if remaining < 0:
                raise ValueError("Currency ledger fills exceed order quantity")
            if remaining > 0:
                pending.append(
                    CurrencyPendingOrder(
                        order_id,
                        command["instrument_id"],
                        command["side"],
                        remaining,
                        slippage[order_id],
                    )
                )
        return fills, tuple(pending)

    def _exposure_snapshot_unlocked(self, instrument_id: str = "EURUSD") -> PortfolioExposure:
        order_rows = self._connection.execute(
            "SELECT order_json FROM paper_orders ORDER BY order_id"
        ).fetchall()
        fill_rows = self._connection.execute(
            "SELECT fill_json FROM paper_fills ORDER BY fill_id"
        ).fetchall()
        if not order_rows and not fill_rows:
            return PortfolioExposure.empty(instrument_id)
        orders = [json.loads(row[0]) for row in order_rows]
        commands = {order["order_id"]: order["command"] for order in orders}
        fills = [_decode_fill(row[0]) for row in fill_rows]
        filled_by_order: dict[str, Decimal] = {}
        filled_net = Decimal(0)
        for fill in fills:
            command = commands.get(fill.order_id)
            if command is None:
                raise ValueError("Paper fill has no order")
            if fill.side != command["side"] or fill.instrument_id != command["instrument_id"]:
                raise ValueError("Fill does not match paper order")
            filled_by_order[fill.order_id] = (
                filled_by_order.get(fill.order_id, Decimal(0)) + fill.quantity
            )
            if fill.instrument_id == instrument_id:
                filled_net += fill.quantity if fill.side == "BUY" else -fill.quantity
        pending_buy = Decimal(0)
        pending_sell = Decimal(0)
        for order in orders:
            if order.get("status") == "REJECTED":
                continue
            command = order["command"]
            remaining = Decimal(command["quantity"]) - filled_by_order.get(order["order_id"], 0)
            if remaining < 0:
                raise ValueError("Paper fills exceed order quantity")
            if command["instrument_id"] != instrument_id:
                continue
            if command["side"] == "BUY":
                pending_buy += remaining
            elif command["side"] == "SELL":
                pending_sell += remaining
            else:
                raise ValueError("Unsupported paper order side")
        payload = json.dumps(
            [[row[0] for row in order_rows], [row[0] for row in fill_rows]],
            separators=(",", ":"),
        ).encode()
        return PortfolioExposure(
            snapshot_id=f"paper:{sha256(payload).hexdigest()}",
            instrument_id=instrument_id,
            filled_net_quantity=filled_net,
            pending_buy_quantity=pending_buy,
            pending_sell_quantity=pending_sell,
        )

    def exposure_snapshot(self, instrument_id: str = "EURUSD") -> PortfolioExposure:
        with self._lock:
            self._connection.execute("BEGIN")
            try:
                snapshot = self._exposure_snapshot_unlocked(instrument_id)
                self._connection.commit()
                return snapshot
            except Exception:
                self._connection.rollback()
                raise

    def record_fill(self, fill: PaperFill) -> PaperFill:
        return self.record_fills((fill,))[0][0]

    def record_fills(self, fills: tuple[PaperFill, ...]) -> tuple[tuple[PaperFill, ...], int]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                recorded: list[PaperFill] = []
                imported = 0
                for fill in fills:
                    result, inserted = self._record_fill_unlocked(fill)
                    recorded.append(result)
                    imported += inserted
                self._connection.commit()
                return tuple(recorded), imported
            except BaseException:
                self._connection.rollback()
                raise

    def _record_fill_unlocked(self, fill: PaperFill) -> tuple[PaperFill, int]:
        encoded = _encode_fill(fill)
        prior = self._connection.execute(
            "SELECT fill_json FROM paper_fills WHERE fill_id = ?", (fill.fill_id,)
        ).fetchone()
        if prior is not None:
            existing = _decode_fill(prior[0])
            if existing != fill:
                raise ValueError("fill id reused with different details")
            return existing, 0
        order = self._connection.execute(
            "SELECT order_json, intent_json FROM paper_orders WHERE order_id = ?",
            (fill.order_id,),
        ).fetchone()
        if order is None:
            raise ValueError("Paper order does not exist")
        order_data = json.loads(order[0])
        command = order_data["command"]
        intent_data = json.loads(order[1])
        if order_data["status"] != "ACCEPTED":
            raise ValueError("Paper order is not accepted")
        if fill.instrument_id != command["instrument_id"] or fill.side != command["side"]:
            raise ValueError("Fill does not match paper order")
        if fill.observed_at < datetime.fromisoformat(intent_data["created_at"]):
            raise ValueError("Fill occurred before intent creation")
        if fill.observed_at > datetime.fromisoformat(command["expires_at"]):
            raise ValueError("Fill occurred after order expiry")
        rows = self._connection.execute(
            "SELECT fill_json FROM paper_fills WHERE order_id = ?", (fill.order_id,)
        ).fetchall()
        filled_quantity = sum((_decode_fill(row[0]).quantity for row in rows), Decimal(0))
        if filled_quantity + fill.quantity > Decimal(command["quantity"]):
            raise ValueError("Fill exceeds order quantity")
        self._connection.execute(
            "INSERT INTO paper_fills VALUES (?, ?, ?)",
            (fill.fill_id, fill.order_id, encoded),
        )
        return fill, 1

    def insert(
        self,
        key: str,
        intent_id: str,
        intent_json: str,
        order_json: str,
        order_id: str,
        *,
        expected_snapshot_id: str,
        max_portfolio_exposure: Decimal,
        currency_reservation: CurrencyReservation | None = None,
        now: datetime | None = None,
        benchmark: DecisionPriceBenchmark | None = None,
    ) -> bool:
        if not max_portfolio_exposure.is_finite() or max_portfolio_exposure <= 0:
            raise ValueError("Portfolio exposure limit must be positive and finite")
        command = json.loads(order_json)["command"]
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                prior = self._connection.execute(
                    "SELECT 1 FROM paper_orders WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if prior is not None:
                    self._connection.commit()
                    return False
                snapshot = self._exposure_snapshot_unlocked(command["instrument_id"])
                if snapshot.snapshot_id != expected_snapshot_id:
                    raise ValueError("Portfolio snapshot changed; re-evaluate risk")
                if snapshot.instrument_id != command["instrument_id"]:
                    raise ValueError("Portfolio instrument mismatch")
                resulting_exposure = snapshot.with_order(
                    command["side"], Decimal(command["quantity"])
                )
                if (
                    resulting_exposure > max_portfolio_exposure
                    and resulting_exposure > snapshot.worst_case_quantity
                ):
                    raise ValueError("Portfolio exposure limit exceeded")
                if currency_reservation is not None:
                    self._validate_currency_reservation(
                        currency_reservation, command, intent_json, order_id, now=now
                    )
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO paper_orders (idempotency_key, intent_id, order_id, intent_json, order_json,benchmark_required) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, intent_id, order_id, intent_json, order_json, int(benchmark is not None)),
                )
                if cursor.rowcount == 0:
                    self._connection.commit()
                    return False
                if benchmark is not None:
                    self._record_benchmark_unlocked(benchmark, order_json, intent_json)
                self._connection.commit()
                return True
            except BaseException:
                self._connection.rollback()
                raise

    def transition(
        self,
        order_id: str,
        *,
        from_status: str,
        to_status: str,
        broker_order_id: str | None = None,
        currency_reservation: CurrencyReservation | None = None,
        now: datetime | None = None,
    ) -> str:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT order_json, intent_json FROM paper_orders WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Paper order does not exist")
                order = json.loads(row[0])
                if order["status"] != from_status:
                    raise ValueError("Paper order state changed")
                if currency_reservation is not None:
                    if (from_status, to_status) != ("PENDING", "DISPATCHING"):
                        raise ValueError("Currency recovery check requires pending dispatch")
                    self._validate_currency_reservation(
                        currency_reservation,
                        order["command"],
                        row[1],
                        order_id,
                        now=now,
                        exclude_pending_order=order_id,
                    )
                order["status"] = to_status
                if broker_order_id is not None:
                    if to_status != "ACCEPTED":
                        raise ValueError("Broker acknowledgement requires acceptance")
                    order["broker_order_id"] = broker_order_id
                encoded = json.dumps(order, sort_keys=True)
                self._connection.execute(
                    "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
                    (encoded, order_id),
                )
                self._connection.commit()
                return encoded
            except BaseException:
                self._connection.rollback()
                raise

    def _validate_currency_reservation(
        self,
        reservation: CurrencyReservation,
        command: dict,
        intent_json: str,
        order_id: str,
        *,
        now: datetime | None,
        exclude_pending_order: str | None = None,
    ) -> None:
        if now is None:
            raise ValueError("Currency reservation requires current time")
        fills, pending = self._currency_ledger_unlocked()
        if exclude_pending_order is not None:
            pending = tuple(item for item in pending if item.order_id != exclude_pending_order)
        candidate = CurrencyPendingOrder(
            order_id,
            command["instrument_id"],
            command["side"],
            Decimal(command["quantity"]),
            Decimal(json.loads(intent_json)["max_slippage"]),
        )
        reservation.validate(fills, pending, candidate, now=now)
