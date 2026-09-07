# Cloud runner container

This container is the deployment shell for the cloud-game runner. It keeps the
browser profile under `/data/cloud`, runs headed Chrome on Xvfb, and keeps a
browser management UI available after startup. The management UI uses host
port `17880` by default; noVNC listens on container and host port `15980` for
first login and troubleshooting. Both published ports are bound to loopback.

For the complete Chinese deployment procedure, requirements, Compose commands,
SMTP setup and troubleshooting, see
[`docs/zh-CN/docker-cloud.md`](../docs/zh-CN/docker-cloud.md).

## Host requirements

- Docker Engine with Docker Compose v2, or Docker Desktop in Linux-container mode.
- An `amd64`/x86-64 host, or a host able to run the `linux/amd64` image.
- Network access to the cloud-game site and image-build dependency sources.
- Two available loopback ports: `17880` for management and `15980` for noVNC.
- At least 1 GiB of container shared memory, supplied by the default Compose file.
  This is not a total-memory minimum; minimum CPU, RAM and disk sizing has not
  yet been established.

## First login

1. Copy `.env.cloud.example` to `.env.cloud`; keep `CLOUD_COMMAND=auto`.
2. Run `docker compose --env-file .env.cloud up --build`.
3. Open `http://127.0.0.1:15980/vnc.html` and complete login manually.
4. Enrollment writes its marker and exits. `unless-stopped` restarts the
   container, and auto mode then selects `serve` while retaining the complete
   browser profile, including cookies and browser storage.
5. Open `http://127.0.0.1:17880` for normal management.

The noVNC port is bound to loopback by default. Do not expose it directly to a
public network; place authenticated TLS access in front of it if remote access
is required.

## Automatic repository updates

The Compose stack includes an `auto-updater` service. Every 24 hours by
default it fetches `origin/master`. When the checked-out revision is behind and
can be fast-forwarded, it pulls the repository, rebuilds `cloud-runner`, and
recreates that service. Tracked local changes or a diverged branch cause the
update to be skipped rather than overwritten.

The management page also shows an `Update Docker` button beside the cloud-game
button. It requests an immediate update through the shared `update-control`
volume; only `auto-updater` receives the repository mount and Docker socket.
The small label below the button is the version baked into the currently
running image. Manual update requests rebuild even when Git is already current,
which also allows retrying a previously failed image build.

The updater mounts `/var/run/docker.sock`, which grants it control over the
host Docker daemon. Enable it only for a repository and image you trust. Change
the interval, remote, or branch with `CLOUD_UPDATE_INTERVAL_SECONDS`,
`CLOUD_UPDATE_POLL_SECONDS`, `CLOUD_UPDATE_REMOTE`, and `CLOUD_UPDATE_BRANCH`.
The updater normally reads
the current Compose project name from its container label; set
`CLOUD_UPDATE_PROJECT_NAME` only when the platform removes that label.

TrueNAS configurations whose Compose file lives outside the repository must
set `CLOUD_UPDATE_COMPOSE_FILE` to its mounted in-container path and mount that
file into `auto-updater`. The repository itself must be mounted read-write at
`/workspace`; tracked local changes intentionally block automatic updates.

Compose uses `223.5.5.5` with `8.8.8.8` as a fallback because some WSL Docker
daemons cannot forward the generated host resolver into containers. Override
`CLOUD_DNS_PRIMARY` and `CLOUD_DNS_SECONDARY` when the deployment network
requires internal DNS.

The image build runs dependency and import checks automatically. An offline
headed-browser probe is also available after building:

```sh
docker run --rm --entrypoint /bin/sh ok-wuthering-waves-cloud:dev \
  /app/docker/browser_smoke_test.sh
```

On a WSL checkout whose Windows-side cache directory has restrictive ACLs, run
the complete Compose build and browser probe through a clean ignored context:

```sh
sudo sh docker/compose_smoke_test.sh .
```

The container selects `docker.playwright_adapter:create_playwright_adapter`.
It retains Playwright's `--no-sandbox` launch argument because Chrome cannot
create its user namespace under Docker's default restrictions. This keeps the
container on `no-new-privileges` without adding privileged capabilities; the
desktop adapter continues to omit the flag and its visible warning.
When Docker Engine runs inside WSL without Docker Desktop, make sure the WSL
distribution remains active. If WSL shuts down after the launching terminal
exits, Docker Engine is restarted and its containers are stopped as well.

The Docker build context excludes local environment files, `data/`, browser
profiles, session databases, cookies, and common secret/key files. Keep login
state in the `/data/cloud` volume; never copy an enrolled browser profile into
an image or override these exclusions when building.

`cloud-data` is the logical Compose volume name. Docker normally prefixes its
actual name with the Compose project name, so keep the project name stable when
moving the checkout or using `--project-name`.

Task settings are also kept out of the image. Compose sets
`OK_WW_CLOUD_CONFIG_DIR=/data/cloud/configs`, so the Linux task runtime reads
and updates its JSON settings inside the same persistent volume.
Treat that directory as sensitive and import an existing configuration only at
runtime; do not remove the `configs` build-context exclusion.

SMTP is disabled in the default Compose deployment, so no secret file is
required. To enable login-failure email notification:

1. Create `secrets/smtp_password` containing only the SMTP password. The
   `secrets/` directory is excluded from Git and the Docker build context.
2. Set the non-secret SMTP fields and
   `OK_WW_SMTP_PASSWORD_SOURCE=./secrets/smtp_password` in `.env.cloud`.
3. Start Compose with both files:

```sh
docker compose --env-file .env.cloud \
  -f compose.yaml -f compose.smtp.yaml up --build
```

The override mounts the password read-only at `/run/secrets/smtp_password` and
passes only that path to the process. Never put the password value itself in an
environment variable. Omitting `compose.smtp.yaml` leaves SMTP disabled and
does not require the secret file.

For local Compose, this secret remains a plaintext host file. Restrict its host
permissions and exclude it from backups as appropriate.

Use `OK_WW_SMTP_USE_SSL=true`, `OK_WW_SMTP_USE_TLS=false` for implicit SSL on
port 465. Use `OK_WW_SMTP_USE_SSL=false`, `OK_WW_SMTP_USE_TLS=true` for STARTTLS
on port 587.

## Long-running management and schedules

The image defaults to the long-running management service:

```dotenv
CLOUD_COMMAND=auto
CLOUD_WEB_HOST_PORT=17880
CLOUD_NOVNC_PORT=15980
CLOUD_RESTART_POLICY=unless-stopped
```

The page exposes the registered one-time and trigger-task configuration from
ok-script, the project's character-code editor, and portable scheduled-task
management. On first startup it creates one enabled `DailyTask` schedule at
04:00 in `Asia/Shanghai`; edit, disable, or delete it from the Schedule page.
Other tasks may be added to the same scheduler instead of using a separate
DailyTask-only timer.

Schedules and task settings are stored under `/data/cloud`, so they survive
container replacement. The older `CLOUD_COMMAND=schedule` and `RUN_AT` mode is
retained for compatibility, but new deployments should use `serve` and manage
all schedules from the browser UI.

Compose checks `/healthz` every 30 seconds in `serve` mode and rotates the
Docker `json-file` log at 10 MiB, retaining five files by default. Override
`CLOUD_LOG_MAX_SIZE` and `CLOUD_LOG_MAX_FILES` if required.

## Current limits

- The real cloud-game flow has completed end to end under Docker Xvfb/noVNC at
  1280×720. Repeat-run throughput, long-session stability and performance on
  slower hosts still need broader measurement.
- The initial image targets `linux/amd64`, matching the Google Chrome package
  used by this Dockerfile. Other architectures need a separate browser image.
- The image pins the Linux runtime, including headless OpenCV, OCR and OpenVINO.
  Qt is deliberately absent because the runner uses ok-script's headless core.
- `ok-script==2.0.7b1` incorrectly declares native Windows input/audio packages
  for every platform. During the image build, the exact upstream wheel is
  checked against a fixed SHA-256 and a copied wheel has only those unused
  dependency declarations removed. Two device-backend aggregate initializers
  are reduced to their platform-neutral base exports because Python executes
  those files before importing a base submodule. All other package source is
  unchanged, `pip check` must pass, and the build imports every configured task.
  Remove this compatibility step after upstream publishes platform markers.
- CAPTCHA, account verification, payment, and other sensitive dialogs remain
  manual operations.
- The management UI and noVNC are safe by default only because both port
  mappings in `compose.yaml` are loopback-only. Add authenticated TLS before
  exposing either endpoint to another host.

The management UI defaults to Simplified Chinese on first visit. A language
already selected in Web settings is preserved. Set
`OK_WW_WEB_DEFAULT_LANGUAGE` when another supported first-visit locale is
required.
