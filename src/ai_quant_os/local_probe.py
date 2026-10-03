"""Bounded-parent HTTP readiness helper for loopback services only."""

import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    try:
        url = sys.argv[1]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or not parsed.port
        ):
            return 2
        with build_opener(ProxyHandler({}), _NoRedirect()).open(url, timeout=1) as response:
            content = response.read(262145)
            if response.status != 200 or len(content) > 262144:
                return 2
        sys.stdout.buffer.write(content)
    except (OSError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
