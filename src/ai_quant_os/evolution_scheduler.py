"""Opt-in local tournament and explicitly queued training-job scheduler."""

import asyncio
from datetime import timezone
import json
import logging
import sqlite3
from pathlib import Path
from threading import Event

from ai_quant_os.contracts import _require_aware
from ai_quant_os.evolution_catalog import EvolutionCatalog
from ai_quant_os.evolution_jobs import EvolutionJobQueue
from ai_quant_os.agent_registry import load_agent_catalog
from ai_quant_os.genome_store import GenomeStore

LOGGER = logging.getLogger(__name__)


class EvolutionScheduler:
    def __init__(
        self,
        catalog: EvolutionCatalog | Path,
        clock,
        *,
        batch_size: int = 4,
        job_queue: EvolutionJobQueue | None = None,
    ) -> None:
        if (
            (type(catalog) is not EvolutionCatalog and not isinstance(catalog, Path))
            or type(batch_size) is not int
            or not 1 <= batch_size <= 128
        ):
            raise ValueError("Evolution scheduler requires its catalog and a bounded batch")
        self._catalog = catalog if type(catalog) is EvolutionCatalog else None
        self._catalog_path = None if self._catalog is not None else catalog
        self.clock = clock
        self.batch_size = batch_size
        if job_queue is not None and type(job_queue) is not EvolutionJobQueue:
            raise ValueError("Evolution scheduler requires its training job queue")
        self.job_queue = job_queue or EvolutionJobQueue(
            self.catalog,
            GenomeStore(
                self.catalog.path.parent / "genomes.sqlite3", registry=load_agent_catalog()
            ),
            clock=clock,
        )
        self.last_cycle_at = None
        self.last_error = None
        self.pending_tournaments = 0
        self.pending_quality_profiles = 0
        self.processed_quality_profiles = 0
        self.pending_evaluation_jobs = 0
        self.processed_evaluation_jobs = 0
        self.last_evaluation_job_state = None
        self.processed_this_cycle = 0
        self.first_cycle_completed = Event()
        self._last_clock = None

    @property
    def catalog(self) -> EvolutionCatalog:
        if self._catalog is None:
            self._catalog = EvolutionCatalog(self._catalog_path)
        return self._catalog

    def status(self) -> dict:
        return {
            "schema_version": "1.0",
            "enabled": True,
            "state": "ERROR"
            if self.last_error
            else "STARTING"
            if self.last_cycle_at is None
            else "CATCHING_UP"
            if self.pending_tournaments
            or self.pending_evaluation_jobs
            or self.pending_quality_profiles
            else "WAITING",
            "last_cycle_at": None if self.last_cycle_at is None else self.last_cycle_at.isoformat(),
            "pending_tournaments": self.pending_tournaments,
            "pending_quality_profiles": self.pending_quality_profiles,
            "processed_quality_profiles": self.processed_quality_profiles,
            "processed_this_cycle": self.processed_this_cycle,
            "pending_evaluation_jobs": self.pending_evaluation_jobs,
            "processed_evaluation_jobs": self.processed_evaluation_jobs,
            "last_evaluation_job_state": self.last_evaluation_job_state,
            "last_error": self.last_error,
            "evaluation_verified": False,
            "promotion_allowed": False,
        }

    def step(self) -> dict:
        now = self.clock()
        _require_aware(now)
        now = now.astimezone(timezone.utc)
        if self._last_clock is not None and now < self._last_clock:
            raise ValueError("Evolution scheduler clock regressed")
        self._last_clock = now
        self.last_cycle_at = now
        self.processed_this_cycle = 0
        self.processed_quality_profiles = 0
        self.processed_evaluation_jobs = 0
        try:
            job = self.job_queue.process_next()
            if job is not None:
                self.processed_evaluation_jobs = 1
                self.last_evaluation_job_state = job.state
            self.pending_evaluation_jobs = sum(
                row.state in {"PENDING", "RUNNING"} for row in self.job_queue.list_jobs()
            )
            pending_quality_profiles = self.catalog.pending_training_quality_profiles(
                limit=self.batch_size
            )
            for protocol_sha256, candidate_genome_ref in pending_quality_profiles:
                profile = self.catalog.training_quality_profile(
                    protocol_sha256, candidate_genome_ref
                )
                self.catalog.register(profile)
                self.processed_quality_profiles += 1
            self.pending_quality_profiles = len(
                self.catalog.pending_training_quality_profiles(limit=1000)
            )
            pending = self.catalog.pending_monthly_tournaments(
                now.strftime("%Y-%m"), limit=self.batch_size
            )
            for protocol_sha256, period in pending:
                tournament = self.catalog.monthly_tournament(protocol_sha256, period)
                self.catalog.register(tournament)
                self.processed_this_cycle += 1
            self.pending_tournaments = len(
                self.catalog.pending_monthly_tournaments(now.strftime("%Y-%m"), limit=1000)
            )
            self.last_error = None
        except (ValueError, TypeError, KeyError, ArithmeticError, OSError, sqlite3.Error) as error:
            self.last_error = "EVOLUTION_CATALOG_UNAVAILABLE"
            LOGGER.error(
                json.dumps(
                    {
                        "event": "evolution_tournament_scheduler_error",
                        "reason": self.last_error,
                        "exception_type": type(error).__name__,
                    }
                )
            )
        self.first_cycle_completed.set()
        return self.status()


async def run_evolution_scheduler(worker: EvolutionScheduler) -> None:
    while True:
        job = asyncio.create_task(asyncio.to_thread(worker.step))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            try:
                await job
            except Exception as error:
                worker.last_error = "EVOLUTION_CATALOG_UNAVAILABLE"
                LOGGER.error(
                    json.dumps(
                        {
                            "event": "evolution_tournament_scheduler_shutdown_error",
                            "exception_type": type(error).__name__,
                        }
                    )
                )
            raise
        except Exception as error:
            worker.last_error = "EVOLUTION_CATALOG_UNAVAILABLE"
            LOGGER.error(
                json.dumps(
                    {
                        "event": "evolution_tournament_scheduler_error",
                        "reason": worker.last_error,
                        "exception_type": type(error).__name__,
                    }
                )
            )
        await asyncio.sleep(60)
