"""Server-owned editable source boundaries for the two implemented shadow agents."""

from types import MappingProxyType


AGENT_SOURCE_PATHS = MappingProxyType(
    {
        ("A02", "0.2.0"): frozenset(
            {"src/ai_quant_os/regime_agent.py", "src/ai_quant_os/regime_features.py"}
        ),
        ("A07", "0.2.0"): frozenset({"src/ai_quant_os/integrity_agent.py"}),
    }
)
