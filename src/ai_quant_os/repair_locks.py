"""Preserved repair-root ownership namespace and public entry point."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ai_quant_os.root_ownership import kernel_root_lock


@contextmanager
def repair_root_lock(root: Path) -> Iterator[Path]:
    with kernel_root_lock(root, lock_name=".repair-owner.lock") as owned:
        yield owned
