"""The poll loop must survive an upstream that goes quiet.

On 2026-08-22 `urlopen` was called without a timeout, myUplink's connection went
silent without a FIN, and the poll thread blocked in one read for 31 hours. The
page served a day-old reading and every health signal stayed green, because they
all asked Flask's main thread — which was fine.

The stub here is that outage in miniature: a socket that accepts a connection and
then says nothing, ever. Before the fix this file does not fail, it *hangs*.

    python tests/poller.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CONFIG = tempfile.mkdtemp(prefix="varmvatten-test-")
os.environ["MYUPLINK_CONFIG_DIR"] = CONFIG
json.dump({"client_id": "x", "client_secret": "y"}, open(f"{CONFIG}/config.json", "w"))
json.dump(
    {"access_token": "t", "expires_in": 99999, "obtained_at": time.time()},
    open(f"{CONFIG}/tokens.json", "w"),
)

import nibe_client  # noqa: E402  (must follow MYUPLINK_CONFIG_DIR)

fails = 0


def check(label, ok, detail=""):
    global fails
    if not ok:
        fails += 1
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{' — ' + str(detail) if detail else ''}")


print("poller against an upstream that never answers:")

# A server that completes the TCP handshake and then never sends a byte. Holding
# the accepted sockets matters: letting them be garbage-collected would close
# them and hand the client an EOF, which is a different (and much kinder) bug.
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(8)
held = []
threading.Thread(target=lambda: [held.append(srv.accept()) for _ in iter(int, 1)], daemon=True).start()

nibe_client.API_BASE = f"http://127.0.0.1:{srv.getsockname()[1]}"
nibe_client.REQUEST_TIMEOUT_SECONDS = 3

started = time.monotonic()
try:
    nibe_client.get_hot_water_temp()
    check("a silent upstream raises instead of hanging", False, "returned normally")
except Exception as exc:
    waited = time.monotonic() - started
    check(
        "a silent upstream raises instead of hanging",
        3 <= waited < 8 and "did not answer within" in str(exc),
        f"after {waited:.1f}s: {exc}",
    )

import app as vv  # noqa: E402  (starts the poll and watchdog threads on import)

vv.POLL_INTERVAL_SECONDS = 1
vv.STALE_AFTER_SECONDS = 3
time.sleep(4)

client = vv.app.test_client()

snapshot = vv._snapshot()
check("an unreachable upstream marks the reading stale", snapshot["stale"] is True, snapshot)

body = client.get("/api/temperature").get_json()
check("/api/temperature reports age and staleness", "age_seconds" in body and "stale" in body, body)

response = client.get("/healthz")
health = response.get_json()
# The distinction that was missing: upstream down is not the app being broken,
# and failing health on it would roll back a good deploy during someone else's
# outage.
check("upstream down leaves the app healthy", response.status_code == 200 and health["status"] == "ok",
      f"{response.status_code} {health['status']}")
check("…while being honest that the data is stale", health["stale"] is True)

# A poll thread that has stopped even *trying* is this app being broken, and is
# the one thing /healthz must report.
vv.WEDGED_AFTER_SECONDS = 2
with vv._state_lock:
    vv._last_attempt = time.monotonic() - 60
response = client.get("/healthz")
check("a wedged poll thread reports 503", response.status_code == 503, response.status_code)
check("…and names what is wrong", response.get_json()["status"] == "poll thread wedged",
      response.get_json()["status"])

print(f"\n{fails} check(s) failed" if fails else "\nall checks passed")
sys.exit(1 if fails else 0)
