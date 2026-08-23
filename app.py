import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template

import nibe_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("varmvatten")

POLL_INTERVAL_SECONDS = 60

# How old a reading may be before the page stops presenting it as the truth.
# Five missed polls: long enough that one flaky request changes nothing, short
# enough that nobody plans a shower around yesterday's number.
STALE_AFTER_SECONDS = 5 * POLL_INTERVAL_SECONDS

# How long the poll thread may go without *completing an iteration* before we
# treat it as wedged and let the container restart us.
#
# This measures attempts, not successes, which is the whole point: myUplink
# being down is not a reason to restart, and would otherwise put us in a
# restart loop for the length of someone else's outage. A thread that has not
# even come round to try is a different thing - it is stuck inside a call that
# will not return, and no amount of waiting fixes it.
#
# An iteration is at most ~3 requests at REQUEST_TIMEOUT_SECONDS plus the
# sleep, so ~2 minutes worst case. Five is comfortably clear of that.
WEDGED_AFTER_SECONDS = 5 * 60

ASSET_VERSION = int(time.time())  # cache-busts static assets on every deploy/restart

app = Flask(__name__)
_state_lock = threading.Lock()
_state = {"temp": None, "unit": "°C", "updated_at": None}

# Monotonic, because this decides whether to kill the process and a clock step
# (NTP, DST) must not be able to do that on its own.
_last_attempt = time.monotonic()
_last_success = None


def _snapshot():
    """The reading plus how old it is. Callers get a plain dict they own."""
    with _state_lock:
        data = dict(_state)
        last_success = _last_success
    age = None if last_success is None else round(time.monotonic() - last_success, 1)
    data["age_seconds"] = age
    # No reading yet counts as stale: the page must not present a blank as fresh.
    data["stale"] = age is None or age > STALE_AFTER_SECONDS
    return data


def poll_loop():
    global _last_attempt, _last_success
    while True:
        with _state_lock:
            _last_attempt = time.monotonic()
        try:
            temp = nibe_client.get_hot_water_temp()
            with _state_lock:
                _state["temp"] = temp
                _state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _last_success = time.monotonic()
            log.info("hot water temp: %s°C", temp)
        except Exception:
            log.exception("failed to poll hot water temperature")
        time.sleep(POLL_INTERVAL_SECONDS)


def watchdog_loop():
    """Restart the process if the poll thread stops coming round.

    Python cannot interrupt a thread blocked in a syscall, so a wedged poller
    cannot be repaired from inside - the only lever is to exit and let
    `restart: unless-stopped` build a new process. That is precisely the manual
    fix that ended the 2026-08-22 outage, minus the 31 hours of waiting for a
    human to notice.

    `os._exit` rather than `sys.exit`: this is not the main thread, so
    SystemExit would be caught by the thread machinery and change nothing.
    """
    while True:
        time.sleep(30)
        with _state_lock:
            since = time.monotonic() - _last_attempt
        if since > WEDGED_AFTER_SECONDS:
            log.error(
                "poll thread has not completed an iteration in %.0fs (limit %ds) - "
                "it is stuck in a call that will not return; exiting so the "
                "container restarts",
                since,
                WEDGED_AFTER_SECONDS,
            )
            os._exit(1)


@app.route("/")
def index():
    return render_template("index.html", v=ASSET_VERSION)


@app.route("/api/temperature")
def temperature():
    return jsonify(_snapshot())


@app.route("/healthz")
def healthz():
    """Is the *app* working - not: is the reading fresh.

    Those are different questions and conflating them was half of why the
    outage went unseen. Upstream being down leaves this healthy and the reading
    stale, so a myUplink outage cannot fail a deploy or flap the container. A
    poll thread that has stopped trying is a fault in here, and says so.
    """
    with _state_lock:
        since = time.monotonic() - _last_attempt
    body = {**_snapshot(), "seconds_since_poll_attempt": round(since, 1)}
    if since > WEDGED_AFTER_SECONDS:
        body["status"] = "poll thread wedged"
        return jsonify(body), 503
    body["status"] = "ok"
    return jsonify(body)


threading.Thread(target=poll_loop, daemon=True).start()
threading.Thread(target=watchdog_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
