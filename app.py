import logging
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template

import nibe_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("varmvatten")

POLL_INTERVAL_SECONDS = 60
ASSET_VERSION = int(time.time())  # cache-busts static assets on every deploy/restart

app = Flask(__name__)
_state_lock = threading.Lock()
_state = {"temp": None, "unit": "°C", "updated_at": None}


def poll_loop():
    while True:
        try:
            temp = nibe_client.get_hot_water_temp()
            with _state_lock:
                _state["temp"] = temp
                _state["updated_at"] = datetime.now(timezone.utc).isoformat()
            log.info("hot water temp: %s°C", temp)
        except Exception:
            log.exception("failed to poll hot water temperature")
        time.sleep(POLL_INTERVAL_SECONDS)


@app.route("/")
def index():
    return render_template("index.html", v=ASSET_VERSION)


@app.route("/api/temperature")
def temperature():
    with _state_lock:
        return jsonify(dict(_state))


threading.Thread(target=poll_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
