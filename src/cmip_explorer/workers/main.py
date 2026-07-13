from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    json.dump({"type": "worker_ready", "request_id": request.get("request_id")}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
