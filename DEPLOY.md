# Deployment

Build happens on GitHub. Deploy happens on the server, by pulling. The server
opens no inbound ports — it only makes an outbound registry pull every five
minutes.

```
push to main ──► build.yml ──► ghcr.io/…:sha-abc1234  (built + smoke tested, NOT live)
                                        │
                       you promote ─────┤  Actions ▸ promote ▸ Run workflow
                       (or: git tag v*) │  retags, does not rebuild
                                        ▼
                               ghcr.io/…:stable
                                        │
                     server polls ──────┤  systemd timer, every 5 min
                                        ▼
                          scripts/deploy.sh ──► health check ──► rollback if bad
```

Pushing to `main` never changes what is running. Promoting does. That is the
whole design.

## One-time setup

Run in order — steps 1–3 must happen before the timer is enabled, because
`deploy.sh` fails until `:stable` exists.

**1. Push these files.**

```bash
git add .github docker-compose.yml docker-compose.dev.yml scripts/deploy.sh .gitignore DEPLOY.md
git commit -m "Add GHCR build/promote pipeline and pull-based deploy agent"
git push
```

Watch the run: `gh run watch`.

**2. Make the GHCR package public.**

The first `build` run creates the package as private. On
<https://github.com/users/lowbit-software/packages/container/varmvatten/settings>
set visibility to **Public**. The repo is already public, so this exposes
nothing new, and it means the server needs no registry credentials at all.

If you would rather keep it private: create a PAT with `read:packages` and run
`docker login ghcr.io -u lowbit-software` once on the box. The stored
credential in `~/.docker/config.json` is all `deploy.sh` needs.

**3. Promote once, manually.**

GitHub ▸ Actions ▸ **promote** ▸ Run workflow ▸ source `main`. Then confirm the
server can see it and deploy it:

```bash
cd ~/varmvatten && ./scripts/deploy.sh
```

That first run replaces the locally-built container with the GHCR one.

**4. Install and enable the timer.**

The units live in `deploy/systemd/` and use the `%h` specifier, so they work for
any user as long as the repo is cloned to `~/varmvatten`.

```bash
install -m 644 deploy/systemd/varmvatten-deploy.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now varmvatten-deploy.timer
sudo loginctl enable-linger "$USER"        # user timers run without a login session
systemctl --user list-timers varmvatten-deploy.timer
```

`enable-linger` is required — without it the timer stops the moment you log out
of SSH.

They are deliberately **user** units, not system units: `docker-compose.yml`
mounts `~/.config/myuplink`, and under a system unit running as root that `~`
would resolve to `/root/.config/myuplink`, so the app would come up without its
myUplink tokens.

After editing anything in `deploy/systemd/`, re-run the `install` and
`daemon-reload` lines — a `git pull` alone does not update the live units.

**5. Optional: require an approval click.**

Repo ▸ Settings ▸ Environments ▸ `production` ▸ Required reviewers ▸ add
yourself. Every promote then waits for you to press Approve in the Actions UI.

## Day-to-day

| Goal | Action |
|---|---|
| Ship what is on `main` | Actions ▸ promote ▸ Run workflow (source: `main`) — **after** `build` is green; it fails rather than shipping the previous commit |
| Ship a version tag | `git tag v1.2.3 && git push --tags` |
| Roll back | Actions ▸ promote ▸ Run workflow (source: `sha-abc1234` of a known-good build) |
| Deploy right now, don't wait 5 min | `systemctl --user start varmvatten-deploy.service` |
| What is actually running? | `cat ~/varmvatten/.deploy.env` |
| Deploy history | `journalctl --user -u varmvatten-deploy -n 100` |
| Change compose/env, not the image | `cd ~/varmvatten && git pull && docker compose --env-file .env --env-file .deploy.env up -d` |
| Local development | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build` |

Available `sha-*` tags are listed on the package page, newest first.

## How safety is enforced

`deploy.sh` pins an **immutable digest** in `.deploy.env` rather than following
the `:stable` tag at runtime, so a container restart or a `docker compose up`
can never silently pick up a different image than the one that was health-checked.

After starting a new image it waits for two independent signals: the compose
healthcheck (the app answers inside the container) and a request to
`127.0.0.1:5000` from the host (the published port actually works). If either
fails within 90s it rewrites `.deploy.env` to the previous digest, brings that
back up, and exits non-zero so `systemctl --user status varmvatten-deploy`
shows red.

A digest that failed is recorded in `.deploy.failed` and skipped on later ticks.
Without that, a bad promote would redeploy and roll back every five minutes
forever. The quarantine clears as soon as a different digest is promoted.

`.env` is never touched by any of this — it stays on the box, gitignored, and
holds `CLOUDFLARE_TUNNEL_TOKEN` plus `APP_UID`/`APP_GID`.

## Expect a tunnel-health alert when you update cloudflared

Cloudflare emails a tunnel-health alert when the connector disconnects, and it
proved **more sensitive than expected**: on 2026-09-27 a clean 16-second restart
during a planned update triggered one, with four edge connections re-registering
immediately afterwards. That is the alert working, not misfiring.

Sensitivity is Cloudflare's to change and yours to configure, so treat the table
below as what was observed rather than a guarantee — but the shape holds, since
it follows from which containers compose actually restarts.

Worth knowing precisely when it happens, so an expected email never has to be
investigated and an unexpected one always does:

| Action | Tunnel restarts? | Alert? |
|---|---|---|
| App deploy (promote + `deploy.sh`) | No — compose reports cloudflared `Running`, not `Recreated` | No |
| Deploy timer tick, nothing to do | No | No |
| Merging a Dependabot cloudflared bump, then applying it (below) | **Yes** | **Yes** |
| Anything actually broken | Yes | Yes |

So the only routine cause is a connector update — roughly monthly, and always
within a minute of you deliberately merging and applying it. An alert arriving
at any other time is real.

Applying a connector bump is a compose change, not a promote — the app image is
untouched. **Both env files, every time:**

```bash
git pull && docker compose --env-file .env --env-file .deploy.env up -d
```

`.deploy.env` is not optional here and is the easiest thing to leave off.
`docker-compose.yml` reads `${APP_IMAGE:-…:stable}`, so without that file the
pinned digest silently becomes the moving `:stable` tag — which is precisely the
drift `deploy.sh` pins a digest to prevent. Same rule as the "Change compose/env,
not the image" row above.

Verified rather than assumed: a no-op `up -d` leaves the connector's start time
unchanged, and the app deploys in this project's history all report cloudflared
as `Running`.

## Failure modes worth knowing

- **The page shows a temperature that is hours old** — the poll thread stopped.
  Ask the app rather than guessing: `curl -s localhost:5000/healthz` reports
  `age_seconds` (how old the reading is) and `seconds_since_poll_attempt` (how
  long since the thread last tried). Those answer different questions and the
  difference is the diagnosis: a large `age_seconds` with a small
  `seconds_since_poll_attempt` means myUplink is failing and we are retrying —
  nothing to fix here. Both large means the thread is wedged, and the watchdog
  should already have exited the process for `restart: unless-stopped` to
  replace it. The page greys the card out and says "Ingen kontakt" whenever the
  reading is stale, so this should be visible before anyone goes looking.

  This is what bit on 2026-08-22: `urlopen` had no timeout, a myUplink
  connection went quiet without a FIN, and the poll thread blocked in one read
  for 31 hours. The page kept serving a confident 58° while the water was 29°,
  and every health signal stayed green because they all asked Flask's main
  thread, which was fine.


- **`cannot pull … is the package published and public`** — step 2 was skipped,
  or the PAT expired.
- **`no previous digest to roll back to`** — only possible on the very first
  deploy, when there is no known-good digest yet. The stack is down; fix
  forward or run the dev overlay.
- **`ROLLBACK FAILED`** — the old image is unhealthy too, so the cause is
  environmental (missing `~/.config/myuplink/tokens.json`, expired NIBE refresh
  token, port conflict) rather than the new build. Check
  `docker compose logs webapp`.
- **Timer silently not running** — almost always `enable-linger`. Check with
  `loginctl show-user jonas | grep Linger`.
