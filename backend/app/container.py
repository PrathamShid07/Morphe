"""Dependency wiring. Long-lived clients (Qdrant, MongoDB, embedding model) are created once per process."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adaptation.controller import AdaptationService
from .common import get_logger, log_event
from .compression.cleanup import CompressionExecutor
from .compression.controller import CompressionService
from .compression.summarizer import ExtractiveSummarizer
from .config import Settings
from .coordinator import MutationCoordinator
from .evaluation.dataset import DatasetStore
from .evaluation.evaluator import Evaluator
from .evaluation.service import EvaluationService
from .evaluation.version_comparator import AcceptancePolicy
from .graph.manager import GraphManager
from .graph.persistence import ArchitecturePersistence
from .ingestion.embedder import CachedEmbedder, Embedder, build_embedder
from .ingestion.pipeline import IngestionService
from .memory.access_tracker import AccessTracker
from .memory.budget_tracker import BudgetTracker
from .memory.manager import MemoryManager
from .memory.modules import ModuleService
from .monitoring.events import EventLog
from .monitoring.metrics import MetricsService
from .reconcile import reconcile_startup
from .retrieval.controller import RetrievalEngine, RetrievalService
from .storage.mongo_store import MongoStore
from .storage.qdrant_store import QdrantStore
from .versioning.rollback import ArchitectureLifecycle
from .versioning.snapshots import VersionStore
from .versioning.validator import LiveValidator

log = get_logger("container")


@dataclass
class AppContext:
    settings: Settings
    embedder: Embedder
    qdrant: QdrantStore
    mongo: MongoStore
    graph: GraphManager
    persistence: ArchitecturePersistence
    coordinator: MutationCoordinator
    events: EventLog
    versions: VersionStore
    validator: LiveValidator
    lifecycle: ArchitectureLifecycle
    modules: ModuleService
    budget: BudgetTracker
    memories: MemoryManager
    ingestion: IngestionService
    tracker: AccessTracker
    retrieval: RetrievalService
    datasets: DatasetStore
    evaluator: Evaluator
    evaluation: EvaluationService
    policy: AcceptancePolicy
    adaptation: AdaptationService
    compression_exec: CompressionExecutor
    compression: CompressionService
    metrics: MetricsService
    startup_report: dict[str, Any] | None = None

    def close(self) -> None:
        for svc in (self.adaptation, self.compression):
            for attr in ("_executor", "_pool"):
                pool = getattr(svc, attr, None)
                if pool is not None:
                    pool.shutdown(wait=True, cancel_futures=True)
        self.qdrant.close()
        self.mongo.close()


def build_context(settings: Settings, *, embedder: Embedder | None = None, qdrant: QdrantStore | None = None,
                  mongo: MongoStore | None = None, reconcile: bool = True) -> AppContext:
    embedder = embedder or build_embedder(settings)
    cached = CachedEmbedder(embedder)
    qdrant = qdrant or QdrantStore.from_settings(settings)
    mongo = mongo or MongoStore.from_settings(settings)
    graph = GraphManager()
    persistence = ArchitecturePersistence(qdrant, mongo, graph)
    coordinator = MutationCoordinator(mongo, settings.mutation_lock_timeout_seconds)
    events = EventLog(mongo)
    versions = VersionStore(mongo, settings.embedding_dimension)
    validator = LiveValidator(qdrant, mongo, graph, persistence)
    lifecycle = ArchitectureLifecycle(settings, qdrant, mongo, persistence, versions, validator, coordinator, events)
    modules = ModuleService(qdrant, mongo, persistence)
    budget = BudgetTracker(mongo, settings)
    memories = MemoryManager(qdrant, mongo, modules, coordinator, budget)
    tracker = AccessTracker(mongo)
    engine = RetrievalEngine(settings, qdrant, lambda: graph.graph)
    datasets = DatasetStore(mongo, settings.eval_dataset_path)
    evaluator = Evaluator(settings, cached, qdrant, engine, datasets, coordinator, mongo)
    lifecycle.evaluator = evaluator
    policy = AcceptancePolicy(settings)
    evaluation = EvaluationService(evaluator, versions, lifecycle, persistence, policy, mongo)
    adaptation = AdaptationService(settings, qdrant, mongo, persistence, versions, lifecycle, validator, evaluator,
                                   policy, coordinator, events, tracker)
    provider = ExtractiveSummarizer(embedder.count_tokens)
    compression_exec = CompressionExecutor(settings, embedder, qdrant, mongo, modules, events, provider)
    compression = CompressionService(settings, mongo, budget, compression_exec, coordinator, events)
    metrics = MetricsService(settings, mongo, qdrant, graph, budget, versions.active_id)

    def after_mutation(trigger: str) -> dict[str, Any]:
        metrics.sample(force=True)
        return compression.check(trigger)

    counter = {"n": 0}

    def on_query(event: dict[str, Any]) -> None:
        counter["n"] += 1
        if counter["n"] % 5 == 0:
            metrics.sample()
        adaptation.on_query_completed(event)

    ingestion = IngestionService(settings, cached, qdrant, mongo, modules, coordinator, budget, after_mutation)
    retrieval = RetrievalService(settings, cached, engine, coordinator, mongo, tracker, versions.active_id, on_query)
    ctx = AppContext(settings, cached, qdrant, mongo, graph, persistence, coordinator, events, versions, validator,
                     lifecycle, modules, budget, memories, ingestion, tracker, retrieval, datasets, evaluator, evaluation,
                     policy, adaptation, compression_exec, compression, metrics)
    if reconcile:
        with coordinator.mutate("reconciliation", meta={"reason": "startup"}):
            ctx.startup_report = reconcile_startup(ctx)
        if not datasets.current().queries:
            datasets.load_fixture()
    log_event(log, "context_ready", memories=budget.usage()["active_memories"], modules=graph.counts()[0])
    return ctx
