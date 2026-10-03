"""One foreground command for the local paper API and built Mission Control."""

import argparse
import json
import math
import signal
import time
from pathlib import Path

from ai_quant_os.local_runtime import LocalSettings, local_application


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local paper API and Mission Control")
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, default=Path("data-local/local-app"))
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--run-seconds", type=float)
    args = parser.parse_args()
    if args.run_seconds is not None and (
        not math.isfinite(args.run_seconds) or not 0.1 <= args.run_seconds <= 60
    ):
        parser.error("Bounded smoke duration must be between 0.1 and 60 seconds")
    settings = LocalSettings(args.checkout, args.state, args.api_port, args.web_port)

    def stop(signum, frame):
        raise KeyboardInterrupt

    original = signal.signal(signal.SIGTERM, stop)
    try:
        with local_application(settings) as children:
            print(
                json.dumps(
                    {
                        "state": "READY",
                        "mode": "PAPER",
                        "api_url": f"http://127.0.0.1:{args.api_port}",
                        "web_url": f"http://127.0.0.1:{args.web_port}",
                    }
                ),
                flush=True,
            )
            deadline = None if args.run_seconds is None else time.monotonic() + args.run_seconds
            while deadline is None or time.monotonic() < deadline:
                if any(child.process.poll() is not None for child in children):
                    raise RuntimeError("Owned local service stopped")
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError):
        parser.exit(
            2,
            "Local application refused or stopped; check dependencies, built frontend, ports and paper storage.\n",
        )
    finally:
        signal.signal(signal.SIGTERM, original)
    print(json.dumps({"state": "STOPPED", "mode": "PAPER"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
