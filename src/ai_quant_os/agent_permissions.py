"""Fixed capability ceilings; manifests cannot grant capital-control authority."""

from types import MappingProxyType

READ_TOOLS = frozenset(
    {
        "WORLD_STATE_READ",
        "HISTORY_READ",
        "MACRO_READ",
        "HEALTH_READ",
        "RESEARCH_MEMORY_READ",
        "STRATEGY_REGISTRY_READ",
        "PORTFOLIO_READ",
        "ATTRIBUTION_READ",
        "TRAINING_READ",
        "HOLDOUT_READ",
    }
)
SANDBOX_TOOLS = frozenset(
    {
        "TRAINING_READ",
        "HOLDOUT_READ",
        "CANDIDATE_CREATE",
        "VALIDATION_RUN",
        "STRESS_RUN",
        "REPLAY_RUN",
        "REPAIR_CANDIDATE_CREATE",
        "EVOLUTION_PLAN_CREATE",
    }
)
ROLE_CEILINGS = MappingProxyType(
    {
        "A01": frozenset({"WORLD_STATE_READ", "HEALTH_READ", "TASK_ORCHESTRATE"}),
        "A02": frozenset({"WORLD_STATE_READ", "HISTORY_READ", "MODEL_REVIEW"}),
        "A03": frozenset({"WORLD_STATE_READ", "MACRO_READ", "MODEL_REVIEW"}),
        "A04": frozenset({"WORLD_STATE_READ", "HISTORY_READ", "MACRO_READ"}),
        "A05": frozenset({"WORLD_STATE_READ", "HISTORY_READ", "HEALTH_READ"}),
        "A06": frozenset({"WORLD_STATE_READ", "MACRO_READ", "PORTFOLIO_READ"}),
        "A07": frozenset({"WORLD_STATE_READ", "HISTORY_READ", "HEALTH_READ", "INCIDENT_PROPOSE"}),
        "A08": frozenset(
            {"WORLD_STATE_READ", "HISTORY_READ", "TRAINING_READ", "RESEARCH_MEMORY_READ"}
        ),
        "A09": frozenset(
            {"WORLD_STATE_READ", "TRAINING_READ", "RESEARCH_MEMORY_READ", "CANDIDATE_CREATE"}
        ),
        "A10": frozenset({"HISTORY_READ", "HOLDOUT_READ", "VALIDATION_RUN"}),
        "A11": frozenset({"HOLDOUT_READ", "STRESS_RUN", "RESEARCH_MEMORY_READ"}),
        "A12": frozenset({"WORLD_STATE_READ", "STRATEGY_REGISTRY_READ", "ATTRIBUTION_READ"}),
        "A13": frozenset({"WORLD_STATE_READ", "STRATEGY_REGISTRY_READ", "PORTFOLIO_READ"}),
        "A14": frozenset({"WORLD_STATE_READ", "PORTFOLIO_READ", "ATTRIBUTION_READ"}),
        "A15": frozenset({"HISTORY_READ", "PORTFOLIO_READ", "REPLAY_RUN", "ATTRIBUTION_READ"}),
        "A16": frozenset({"HEALTH_READ", "TRAINING_READ", "REPAIR_CANDIDATE_CREATE"}),
        "A17": frozenset(
            {"HEALTH_READ", "TRAINING_READ", "ATTRIBUTION_READ", "EVOLUTION_PLAN_CREATE"}
        ),
        "A18": frozenset({"WORLD_STATE_READ", "HEALTH_READ", "PORTFOLIO_READ", "INCIDENT_PROPOSE"}),
    }
)
KNOWN_TOOLS = frozenset().union(*ROLE_CEILINGS.values())
