# Varmvatten

A tiny web app that shows the **hot water temperature** of a NIBE F1155
heat pump as one big shower symbol:

| Temperature   | Symbol            | Colour |
| ------------- | ----------------- | ------ |
| `< 30 °C`     | ❄️ Kallt (cold)   | blue   |
| `30 – 45 °C`  | ⏱️ Ljummet (warm) | yellow |
| `> 45 °C`     | ♨️ Varmt (hot)    | red    |

The page updates itself while left open (no manual refresh). Press and
**hold** the card for about half a second to reveal the exact temperature
and the time it was last read; release to hide it again (works with both
mouse and touch). Until the first reading arrives, the page shows the hot
state.

It runs in Docker and is published to the internet through a Cloudflare
Tunnel — no port forwarding, no inbound ports opened on the host.

## How it works

```
NIBE F1155 ──▶ myUplink cloud API ──▶ webapp (Flask)  ──▶ Cloudflare Tunnel ──▶ varmvatten.<domain>
                (OAuth2, polled          background poller       (cloudflared
                 every 60 s)             + JSON API + page        container)
```

The F1155 has no usable local API without the optional MODBUS40 accessory,
so data is read from NIBE's **myUplink** cloud API instead. The app polls
parameter `50325` ("Hot water temp.") once a minute, caches the last value,
and serves it to the browser, which repaints when the value crosses a
threshold.

## Layout

```
varmvatten/
├── app.py               # Flask app: background poller + /api/temperature + page
├── nibe_client.py       # myUplink OAuth token handling + hot-water lookup
├── requirements.txt
├── Dockerfile
├── docker-compose.yml   # webapp + cloudflared services (pulls the promoted image)
├── docker-compose.dev.yml # overlay: build from source instead of pulling
├── .env.example         # template for .env (git-ignored, chmod 600)
├── DEPLOY.md            # build/promote pipeline and the server's deploy agent
├── .github/workflows/   # build.yml (build + smoke test), promote.yml (retag :stable)
├── deploy/systemd/      # user units that poll for a promoted image every 5 min
├── scripts/
│   ├── nibe_login.py    # one-time interactive myUplink OAuth login
│   └── deploy.sh        # server-side pull deploy, health-gated with rollback
├── templates/
│   └── index.html
└── static/
    ├── style.css
    ├── app.js
    ├── cold.png / medium.png / hot.png   # transparent shower cards
    ├── favicon.png
    └── apple-touch-icon.png
```

## Prerequisites

1. **A myUplink account** with the heat pump registered (the same account
   used by the NIBE app).
2. **A myUplink API application** registered at <https://dev.myuplink.com>
   (Applications → Create) with:
   - Scopes: `READSYSTEM offline_access`
   - Redirect URI: `http://localhost:8080/callback` — it never needs to be
     reachable (see the login script), it just has to match. To use another
     URI, register it and set `"redirect_uri"` in `config.json`.
3. **Docker** with the Compose plugin.
4. A domain managed in **Cloudflare** for the public hostname.

### One-time myUplink login

Authentication and the initial browser login are handled by
`scripts/nibe_login.py` (stdlib only, no dependencies). First create
`~/.config/myuplink/config.json` with your API application's credentials
(and `chmod 600` it):

```json
{ "client_id": "...", "client_secret": "..." }
```

Then run the script once on the host:

```bash
python3 scripts/nibe_login.py --list
```

It prints an authorization URL — open it, log in, approve the scopes, then
paste the resulting redirect URL back into the terminal. This writes a
cached refresh token to `~/.config/myuplink/tokens.json`. As a bonus,
`--list` dumps every data point your device exposes, so you can verify the
hot-water parameter ID for your model.

The web app mounts that config directory read/write (at `/config` inside
the container) and only ever needs to **refresh** the token from then on —
it never does an interactive login itself. If the refresh token is ever
revoked (e.g. long disuse), rerun the login script on the host to
re-authenticate.

> Note: the myUplink token endpoint sits behind Cloudflare bot protection,
> so requests must send a browser-like `User-Agent` (already handled in
> `nibe_client.py`).

## Configuration

`~/.config/myuplink/config.json`:

```json
{ "client_id": "...", "client_secret": "..." }
```

(Optionally add `"redirect_uri": "..."` if your registered redirect URI
differs from the default `http://localhost:8080/callback`.)

`.env` in this repo — copy `.env.example`, fill in, `chmod 600 .env`:

```
CLOUDFLARE_TUNNEL_TOKEN=<token from the Cloudflare Zero Trust dashboard>
APP_UID=<uid of the host user that owns ~/.config/myuplink; run: id -u>
APP_GID=<its gid; run: id -g>
```

## Cloudflare Tunnel setup

Done from the dashboard (no `cloudflared login` needed):

1. **Zero Trust → Networks → Tunnels → Create a tunnel**, connector type
   *Cloudflared*, name it `varmvatten`.
2. Copy the tunnel **token** from the shown `docker run … --token <TOKEN>`
   snippet into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`.
3. **Public Hostname** tab: subdomain `varmvatten`, your domain, service
   type `HTTP`, URL `webapp:5000` (the compose service name).
4. Save — Cloudflare creates the DNS record automatically.

## Run

Locally, building from source:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

On the server, `docker compose up -d` instead pulls the image promoted to
`ghcr.io/lowbit-software/varmvatten:stable` — see [DEPLOY.md](DEPLOY.md).

- `webapp` is published only on `127.0.0.1:5000` (loopback) for local
  testing; the public entry point is the tunnel.
- `cloudflared` makes outbound-only connections to Cloudflare's edge.

## Verify

```bash
docker compose logs -f webapp          # expect "hot water temp: NN.N°C" within ~60 s
curl http://127.0.0.1:5000/api/temperature
curl https://varmvatten.<domain>/api/temperature
```

## Endpoints

| Route               | Description                                        |
| ------------------- | -------------------------------------------------- |
| `GET /`             | The full-screen symbol page                        |
| `GET /api/temperature` | JSON `{ "temp", "unit", "updated_at" }`         |

## Notes

- **Poll interval** and thresholds are constants at the top of `app.py`
  (`POLL_INTERVAL_SECONDS`) and in `static/app.js` (`categoryFor`).
- Static assets are cache-busted with a `?v=<start-time>` query on every
  container restart, so updates always reach the browser without a manual
  hard refresh.
- The container runs as `APP_UID:APP_GID` from `.env` to match the host
  user that owns `~/.config/myuplink`, keeping token-file ownership
  consistent.
- Different heat pump model? Run `scripts/nibe_login.py --list` and change
  `HOT_WATER_PARAMETER_ID` in `nibe_client.py` if your hot-water parameter
  differs from `50325`.
