"""Private launch wrapper for the trusted installed paper API."""

from copy import deepcopy
import logging
import os
import sys
from threading import Thread
from pathlib import Path

import uvicorn

from ai_quant_os.root_ownership import kernel_root_lock


class RedactedServerExceptionFilter(logging.Filter):
    """Prevent Uvicorn from formatting raw exception text and traceback frames."""

    def filter(self, record):
        if record.exc_info or record.exc_text or record.stack_info:
            record.msg = "ASGI exception details withheld"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _server_log_config():
    config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["filters"] = {
        **config.get("filters", {}),
        "ai_quant_safe_exception": {"()": RedactedServerExceptionFilter},
    }
    config["handlers"]["default"]["filters"] = ["ai_quant_safe_exception"]
    return config


def main() -> None:
    port = int(sys.argv[1])
    nonce = os.environ["AI_QUANT_LOCAL_INSTANCE"]
    if not 1 <= port <= 65535 or len(nonce) != 32:
        raise ValueError("Invalid local API launch identity")
    root = Path(os.environ["AI_QUANT_PAPER_DB"]).resolve().parent
    with kernel_root_lock(root, lock_name=".local-api-owner.lock"):
        _serve(port, nonce)


def _serve(port: int, nonce: str) -> None:
    from ai_quant_os.api import app

    @app.get("/_local/ready", include_in_schema=False)
    def ready():
        return {"instance": nonce, "mode": "PAPER"}

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
            log_config=_server_log_config(),
        )
    )

    def parent_closed():
        sys.stdin.buffer.read(1)
        server.should_exit = True

    Thread(target=parent_closed, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
