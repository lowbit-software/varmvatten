"""myUplink API client - OAuth token handling plus hot water temperature lookup.

Relies on an existing refresh token at <config dir>/tokens.json (obtained via
the browser login flow in scripts/nibe_login.py) so this module only ever
needs to hit the token-refresh endpoint, never the interactive authorization
flow.

The config dir defaults to ~/.config/myuplink and can be overridden with the
MYUPLINK_CONFIG_DIR environment variable (the Docker container mounts it at
/config).
"""
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error

API_BASE = "https://api.myuplink.com"
HOT_WATER_PARAMETER_ID = 50325  # "Hot water temp." - confirmed via --list

CONFIG_DIR = os.environ.get("MYUPLINK_CONFIG_DIR") or os.path.expanduser("~/.config/myuplink")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
TOKENS_PATH = os.path.join(CONFIG_DIR, "tokens.json")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def http_request(url, data=None, headers=None, method=None):
    headers = {**DEFAULT_HEADERS, **(headers or {})}
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw_response": raw}


def refresh(client_id, client_secret, tokens):
    status, body = http_request(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        return None
    body["obtained_at"] = time.time()
    save_json(TOKENS_PATH, body)
    return body


def get_access_token(client_id, client_secret):
    tokens = load_json(TOKENS_PATH)
    if not tokens:
        raise RuntimeError(
            f"No cached tokens at {TOKENS_PATH}. Run "
            "scripts/nibe_login.py once on the host to log in first."
        )
    age = time.time() - tokens.get("obtained_at", 0)
    if age < tokens.get("expires_in", 0) - 60:
        return tokens["access_token"]
    refreshed = refresh(client_id, client_secret, tokens)
    if not refreshed:
        raise RuntimeError(
            "Token refresh failed - refresh token may be revoked/expired. "
            "Rerun scripts/nibe_login.py on the host to reauthenticate."
        )
    return refreshed["access_token"]


def api_get(path, token):
    status, body = http_request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    if status != 200:
        raise RuntimeError(f"API GET {path} failed ({status}): {body}")
    return body


def find_device_id(token):
    systems = api_get("/v2/systems/me", token)
    for system in systems.get("systems", []):
        for device in system.get("devices", []):
            return device["id"]
    raise RuntimeError("No devices found on this myUplink account.")


def get_hot_water_temp():
    config = load_json(CONFIG_PATH)
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError(f"Missing client_id/client_secret in {CONFIG_PATH}")

    token = get_access_token(client_id, client_secret)
    device_id = find_device_id(token)
    for p in api_get(f"/v2/devices/{device_id}/points", token):
        if str(p.get("parameterId")) == str(HOT_WATER_PARAMETER_ID):
            return float(p["value"])
    raise RuntimeError(f"parameter {HOT_WATER_PARAMETER_ID} not found")
