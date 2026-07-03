#!/usr/bin/env python3
"""One-time browser login against the myUplink API for a NIBE heat pump.

Creates the cached refresh token that the varmvatten webapp needs. Also
handy standalone for finding parameter IDs on your device (--list).

Setup (one-time):
  1. Go to https://dev.myuplink.com, log in with your myUplink account,
     and create a new application:
       - Redirect URI: http://localhost:8080/callback
         (must exactly match the redirect URI used by this script; it does
         NOT need to be reachable - nothing needs to listen on it, see
         authorize(). To use a different URI, register it and put it in
         config.json as "redirect_uri".)
       - Scopes: READSYSTEM offline_access
  2. Copy the Client ID and Client Secret it gives you into
     ~/.config/myuplink/config.json, e.g.:
       {"client_id": "...", "client_secret": "..."}
  3. Run this script. The first run prints a login URL: open it in any
     browser, log in, approve the scopes, then paste the resulting
     (probably failed-to-load) redirect URL back into the terminal.
     After that it refreshes tokens automatically - no browser needed again
     until the refresh token itself expires.

Usage:
  python3 scripts/nibe_login.py            # print detected hot water temperature(s)
  python3 scripts/nibe_login.py --list     # dump all points so you can find the right parameter ID
"""
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.parse
import urllib.error

API_BASE = "https://api.myuplink.com"
# Doesn't need to be reachable - the browser's address bar shows this URL (with
# the code) even when it fails to load, and that's what we read the code from.
# Override with a "redirect_uri" key in config.json; it must exactly match a
# redirect URI registered on your application at https://dev.myuplink.com.
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "READSYSTEM offline_access"

CONFIG_DIR = os.environ.get("MYUPLINK_CONFIG_DIR") or os.path.expanduser("~/.config/myuplink")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
TOKENS_PATH = os.path.join(CONFIG_DIR, "tokens.json")

HOT_WATER_PARAMETER_ID = 50325  # "Hot water temp." - confirmed via --list


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


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


def authorize(client_id, client_secret, redirect_uri):
    state = uuid.uuid4().hex
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    url = f"{API_BASE}/oauth/authorize?{urllib.parse.urlencode(params)}"
    print("1. Open this URL in a browser and log in / grant access:\n")
    print(url, "\n")
    print("2. It will try to redirect to a URL starting with:")
    print(f"   {redirect_uri}")
    print("   That page will likely fail to load - that's fine, nothing needs to")
    print("   be listening there. Just copy the FULL URL from the address bar")
    print("   (it contains the code) and paste it below.\n")
    pasted = input("Paste the redirected URL here: ").strip()
    query = urllib.parse.urlparse(pasted).query
    params_back = urllib.parse.parse_qs(query)
    if params_back.get("state", [None])[0] != state:
        sys.exit("State mismatch - pasted URL doesn't match this login attempt.")
    if "code" not in params_back:
        sys.exit(f"No authorization code in pasted URL: {params_back.get('error')}")
    code = params_back["code"][0]

    status, body = http_request(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        sys.exit(f"Token exchange failed ({status}): {body}")
    body["obtained_at"] = time.time()
    save_json(TOKENS_PATH, body)
    return body


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


def get_access_token(client_id, client_secret, redirect_uri):
    tokens = load_json(TOKENS_PATH)
    if tokens:
        age = time.time() - tokens.get("obtained_at", 0)
        if age < tokens.get("expires_in", 0) - 60:
            return tokens["access_token"]
        refreshed = refresh(client_id, client_secret, tokens)
        if refreshed:
            return refreshed["access_token"]
    tokens = authorize(client_id, client_secret, redirect_uri)
    return tokens["access_token"]


def api_get(path, token):
    status, body = http_request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    if status != 200:
        sys.exit(f"API GET {path} failed ({status}): {body}")
    return body


def find_device_id(token):
    systems = api_get("/v2/systems/me", token)
    for system in systems.get("systems", []):
        for device in system.get("devices", []):
            return device["id"]
    sys.exit("No devices found on this myUplink account.")


def main():
    config = load_json(CONFIG_PATH)
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    redirect_uri = config.get("redirect_uri", DEFAULT_REDIRECT_URI)
    if not client_id or not client_secret:
        sys.exit(f"Missing client_id/client_secret in {CONFIG_PATH}")

    token = get_access_token(client_id, client_secret, redirect_uri)
    device_id = find_device_id(token)
    points = api_get(f"/v2/devices/{device_id}/points", token)

    if "--list" in sys.argv:
        for p in points:
            print(f"{p.get('parameterId'):>8}  {p.get('parameterName'):<30} "
                  f"{p.get('value')} {p.get('parameterUnit', '')}")
        return

    for p in points:
        if str(p.get("parameterId")) == str(HOT_WATER_PARAMETER_ID):
            print(f"{p.get('parameterName')}: {p.get('value')} {p.get('parameterUnit', '')}")
            return
    sys.exit(f"Parameter {HOT_WATER_PARAMETER_ID} not found. Run with --list to check point IDs.")


if __name__ == "__main__":
    main()
