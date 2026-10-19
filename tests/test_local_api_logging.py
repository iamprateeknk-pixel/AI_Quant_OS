"""The local Uvicorn boundary must not print exception contents to its logs."""

import io
import logging
import subprocess
import sys

from ai_quant_os.local_api import RedactedServerExceptionFilter, _server_log_config


def test_uvicorn_exception_filter_removes_exception_message_and_stack():
    caught = RuntimeError("operator-token-private-value")
    try:
        raise caught
    except RuntimeError:
        import sys

        exception_info = sys.exc_info()

    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "Exception in ASGI application",
        (),
        exception_info,
    )
    assert RedactedServerExceptionFilter().filter(record)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.emit(record)

    assert "ASGI exception details withheld" in stream.getvalue()
    assert "operator-token-private-value" not in stream.getvalue()
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None


def test_local_uvicorn_default_handler_uses_the_redaction_filter():
    config = _server_log_config()
    assert "ai_quant_safe_exception" in config["filters"]
    assert config["handlers"]["default"]["filters"] == ["ai_quant_safe_exception"]
    assert "default" in config["loggers"]["uvicorn"]["handlers"]
    assert config["loggers"]["uvicorn.error"].get("propagate", True)


def test_filter_leaves_non_exception_server_messages_unchanged():
    record = logging.LogRecord("uvicorn.error", logging.WARNING, __file__, 1, "safe", (), None)
    assert RedactedServerExceptionFilter().filter(record)
    assert record.msg == "safe" and record.exc_info is None


def test_uvicorn_configured_error_logger_emits_redacted_exception_output():
    script = """
import logging
import logging.config
from ai_quant_os.local_api import _server_log_config
logging.config.dictConfig(_server_log_config())
try:
    raise RuntimeError('operator-token-private-value')
except RuntimeError:
    logging.getLogger('uvicorn.error').exception('Exception in ASGI application')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "ASGI exception details withheld" in result.stderr
    assert "operator-token-private-value" not in result.stderr
    assert "Traceback" not in result.stderr
