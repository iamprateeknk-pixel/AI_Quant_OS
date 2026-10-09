"""Bounded public inspection of retained demo sync journals; no native dispatch."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from ai_quant_os.mt5_account_sync import DemoSyncRequest
from ai_quant_os.mt5_demo_gateway import utc
from ai_quant_os.mt5_demo_observation import ObservationModel
from ai_quant_os.mt5_sync_controller import DemoSyncController, SyncHealth


class ProjectionInspection(ObservationModel):
    state: Literal["MATCHED", "EMPTY", "MISMATCH", "UNAVAILABLE", "UNVERIFIED_CURSOR"]
    receipts: int = Field(default=0, ge=0, le=1000, strict=True)
    known_deals: int = Field(default=0, ge=0, le=4096, strict=True)
    history_start: datetime | None = None
    history_through: datetime | None = None
    quote_context: Literal["FRESH", "STALE"] | None = None


class PendingReceiptInspection(ObservationModel):
    state: Literal["NOT_PENDING", "FINISHED", "UNAVAILABLE"]
    receipt_ref: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    report_state: Literal["CAPTURED", "WITHHELD"] | None = None
    report_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z_]{0,31}$")
    gateway_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z_]{0,31}$")


class SyncInspection(ObservationModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_kind: Literal["LOCAL_UNVERIFIED_JOURNAL_INSPECTION"] = (
        "LOCAL_UNVERIFIED_JOURNAL_INSPECTION"
    )
    state: Literal["READABLE", "PARTIAL", "UNAVAILABLE"]
    inspected_at: datetime
    controller_state: Literal["AVAILABLE", "UNAVAILABLE"]
    health: SyncHealth | None = None
    gateway_custody: Literal["ALL_FINISHED", "CUSTODY_UNCERTAIN", "STORAGE_UNAVAILABLE"]
    projection: ProjectionInspection
    pending_receipt: PendingReceiptInspection
    clock_condition: Literal["AT_OR_AFTER_STATE", "BEFORE_STATE", "UNAVAILABLE"]
    configured_capture_enabled: bool = Field(strict=True)
    may_capture: Literal[False] = False
    may_submit_orders: Literal[False] = False
    may_affect_portfolio: Literal[False] = False
    native_attestation: Literal["UNVERIFIED"] = "UNVERIFIED"

    @model_validator(mode="after")
    def utc_inspection(self):
        utc(self.inspected_at)
        return self


def _projection(controller, health):
    try:
        view = controller.projection.view(controller.connection)
        if view is None:
            return ProjectionInspection(
                state="MISMATCH"
                if health is not None and health.last_history_end is not None
                else "EMPTY"
            )
        state = (
            "UNVERIFIED_CURSOR"
            if health is None
            else ("MATCHED" if view.batch.history_end == health.last_history_end else "MISMATCH")
        )
        return ProjectionInspection(
            state=state,
            receipts=view.receipts,
            known_deals=view.known_deals,
            history_start=view.history_start,
            history_through=view.history_through,
            quote_context=view.quote_context,
        )
    except Exception:
        return ProjectionInspection(state="UNAVAILABLE")


def _receipt(controller, health):
    if health is None:
        return PendingReceiptInspection(state="UNAVAILABLE")
    if health.pending_attempt_id is None:
        return PendingReceiptInspection(state="NOT_PENDING")
    try:
        request = DemoSyncRequest(
            connection=controller.connection,
            history_start=health.pending_history_start,
            history_end=health.pending_history_end,
        )
        reference = controller.gateway.receipt_reference(request, health.pending_attempt_id)
        reply = controller.gateway.replay_existing(request, health.pending_attempt_id)
        return PendingReceiptInspection(
            state="FINISHED" if reference is not None else "UNAVAILABLE",
            receipt_ref=reference,
            report_state=reply.report.state if reply.report is not None else None,
            report_code=reply.report.code if reply.report is not None else None,
            gateway_code=reply.code,
        )
    except Exception:
        return PendingReceiptInspection(state="UNAVAILABLE")


def inspect_sync(
    controller: DemoSyncController, *, clock=lambda: datetime.now(timezone.utc)
) -> SyncInspection:
    now = utc(clock())
    health = controller.inspect_health()
    try:
        custody = controller.gateway.custody_status()
    except Exception:
        custody = "STORAGE_UNAVAILABLE"
    projection = _projection(controller, health)
    failures = sum(
        (health is None, custody == "STORAGE_UNAVAILABLE", projection.state == "UNAVAILABLE")
    )
    state = "READABLE" if failures == 0 else "UNAVAILABLE" if failures == 3 else "PARTIAL"
    return SyncInspection(
        state=state,
        inspected_at=now,
        controller_state="AVAILABLE" if health is not None else "UNAVAILABLE",
        health=health,
        gateway_custody=custody,
        projection=projection,
        pending_receipt=_receipt(controller, health),
        clock_condition="UNAVAILABLE"
        if health is None
        else ("BEFORE_STATE" if now < health.observed_at else "AT_OR_AFTER_STATE"),
        configured_capture_enabled=controller.connection.enabled and controller.schedule.enabled,
    )
