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
| Ship what is on `main` | Actions ▸ promote ▸ Run workflow (source: `main`) |
| Ship a version tag | `git tag v1.2.3 && git push --tags` |
| Roll back | Actions ▸ promote ▸ Run workflow (source: `sha-abc1234` of a known-good build) |
| Deploy right now, don't wait 5 min | `systemctl --user start varmvatten-deploy.service` |
| What is actually running? | `cat ~/varmvatten/.deploy.env` |
| Deploy history | `journalctl --user -u varmvatten-deploy -n 100` |
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

## Failure modes worth knowing

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
