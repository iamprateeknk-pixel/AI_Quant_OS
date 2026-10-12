# Architecture

The current package has three dependency levels:

1. `contracts.py`, `market.py`, `portfolio.py`, `risk.py`, `backtest.py`: deterministic domain policy and immutable values.
2. `execution.py`, `broker.py`, `order_store.py`, `paper_state_store.py`: paper order lifecycle, simulator and SQLite persistence.
3. `api.py`: HTTP schemas, authorization boundary and composition root.

Domain modules must not import FastAPI, Pydantic, broker SDKs, or infrastructure. The only broker route in this slice is the literal `PAPER`. A future external broker adapter requires an explicit contract and release-gate review.

The machine-checked boundary for the five deterministic modules above is declared in
`architecture.json`. `tests/test_architecture_broker_boundary.py` enforces those
forbidden import roots, including literal `importlib` and `__import__` calls. The
declaration covers this named domain slice; it does not claim complete package-wide
layer classification or detect imports assembled dynamically at runtime.
