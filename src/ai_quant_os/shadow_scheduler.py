"""Opt-in local scheduler for forward PAPER shadow observations."""

import asyncio
from datetime import timezone
from hashlib import sha256
import json
import logging

from ai_quant_os.contracts import _require_aware
from ai_quant_os.market_data import BarDataset
from ai_quant_os.shadow_pipeline import observe_shadow

LOGGER = logging.getLogger(__name__)


class ShadowScheduler:
    def __init__(self, shadow_store, market_store, candidates, strategies, clock, integrity):
        self.shadow_store = shadow_store
        self.market_store = market_store
        self.candidates = candidates
        self.strategies = strategies
        self.clock = clock
        self.integrity = integrity
        self.last_error = None
        self.last_cycle_at = None
        self.processed_batches = 0
        self.processed_shadows = 0
        self.assignment_count = 0
        self._last_clock = None

    def status(self):
        return {
            "schema_version": "1.0",
            "enabled": True,
            "state": "ERROR" if self.last_error else "WAITING",
            "assignment_count": self.assignment_count,
            "processed_shadows": self.processed_shadows,
            "processed_batches": self.processed_batches,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
            "promotion_allowed": False,
            "may_submit_orders": False,
        }

    def step(self):
        now = self.clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError("Shadow scheduler clock regressed")
        self._last_clock = now
        self.last_cycle_at = now
        if not self.integrity():
            self.last_error = "PAPER_AUDIT_INTEGRITY_FAILED"
            return self.status()

        try:
            shadow_ids = self.shadow_store.assigned_ids()
            self.assignment_count = len(shadow_ids)
            for shadow_id in shadow_ids:
                assignment = self.shadow_store.assignment(shadow_id)
                if self.shadow_store.retirement(shadow_id) is not None:
                    continue
                previous = self.shadow_store.latest(shadow_id)
                cursor = (
                    assignment.assigned_at
                    if previous is None
                    else previous.source_bars[-1].observed_at
                )
                instrument_id = assignment.candidate.report.plan.holdout_scope.instrument_id
                remaining = 4096 if previous is None else 4096 - len(previous.source_bars)
                stored = self.market_store.read_bars_page(
                    instrument_id, cursor, now, limit=min(4096, max(2, remaining + 1))
                )
                bars = tuple(bar for bar in stored if bar.observed_at > cursor)
                if not bars:
                    continue
                if remaining < 2:
                    self.last_error = "SHADOW_SOURCE_LIMIT_REACHED"
                    return self.status()
                bars = bars[:remaining]
                # Every observation needs two forward points. Leave a short batch
                # pending for the next cycle instead of recording a failed report.
                if len(bars) < 2:
                    continue
                fingerprint = BarDataset(bars).fingerprint
                command_id = (
                    "auto-" + sha256(f"{shadow_id}:{fingerprint}".encode()).hexdigest()[:32]
                )
                report = observe_shadow(
                    shadow_id,
                    command_id,
                    bars,
                    self.shadow_store,
                    self.candidates,
                    self.strategies,
                )
                if report.outcome != "OBSERVED":
                    self.last_error = "SHADOW_OBSERVATION_FAILED"
                    return self.status()
                self.processed_batches += 1
                self.processed_shadows += 1
            self.last_error = None
        except (ValueError, RuntimeError, ArithmeticError, OSError, KeyError) as error:
            self.last_error = "SHADOW_SOURCE_OR_EXECUTION_UNAVAILABLE"
            LOGGER.error(
                json.dumps(
                    {
                        "event": "paper_shadow_scheduler_error",
                        "reason": self.last_error,
                        "exception_type": type(error).__name__,
                    }
                )
            )
        return self.status()


async def run_shadow_scheduler(worker):
    delay = 1
    while True:
        job = asyncio.create_task(asyncio.to_thread(worker.step))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            try:
                await job
            except Exception as error:
                worker.last_error = "SHADOW_SOURCE_OR_EXECUTION_UNAVAILABLE"
                LOGGER.error(
                    json.dumps(
                        {
                            "event": "paper_shadow_scheduler_shutdown_error",
                            "exception_type": type(error).__name__,
                        }
                    )
                )
            raise
        except Exception as error:
            worker.last_error = "SHADOW_SOURCE_OR_EXECUTION_UNAVAILABLE"
            LOGGER.error(
                json.dumps(
                    {
                        "event": "paper_shadow_scheduler_error",
                        "reason": worker.last_error,
                        "exception_type": type(error).__name__,
                    }
                )
            )
        await asyncio.sleep(delay)
        delay = min(60, delay * 2) if worker.last_error is not None else 5
