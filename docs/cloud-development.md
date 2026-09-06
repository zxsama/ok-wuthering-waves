# Cloud game runner development

The cloud integration is intentionally kept under `extensions/cloud`. Existing
gameplay tasks remain unchanged until the browser frame and input bridge has
been proven with the real streaming client.

## Verified public flow

Verified against `https://mc.kurogames.com/cloud/` on 2026-09-06 using a clean,
isolated Chromium profile:

1. The page loads at `https://mc.kurogames.com/cloud/#/` with title
   `云·鸣潮 - Wuthering Waves`.
2. A signed-out visit presents the home page and a `开始游戏` button.
3. The login UI is a cross-origin iframe from
   `https://usercenter.kurogames.com/pro/h5_sdk/index.html`.
4. The current iframe presents phone number, verification code, agreement and
   login controls. These controls are always left to the user.
5. The site creates application, device, analytics and RTC entries in both
   local storage and session storage. A persistent browser profile is therefore
   required; exporting cookies alone is not sufficient.
6. After login, the site may display a full-page version-update reward overlay
   whose instruction is `点击空白区域关闭`; it must be dismissed before clicking
   `进入游戏`.
7. The current account flow displayed a node selection dialog with latency and
   load status, followed by an `进入游戏（60）` countdown, a queueing phase, a
   launch phase, and finally the streaming game surface.
8. `window.GameRunning` becomes true before the `游戏启动中，请耐心等待` overlay
   disappears. The adapter therefore treats that visible overlay as launching
   even when a media surface already exists.
9. End-of-run cleanup closes the browser context directly. It does not depend
   on the cloud player's hidden in-game exit toolbar.

Future page variants should be added to the adapter together with a
fixture-based regression test. Do not copy credentials, cookies, tokens, phone
numbers or storage values into fixtures.

## Commands

Install the cloud optional dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[cloud]"
```

First enrollment must use a visible browser:

```powershell
.\.venv\Scripts\python.exe -m extensions.cloud enroll
```

An unattended run requires both the real page adapter and the existing-task
bridge. On Windows, the default runner connects the active cloud tab to the repository's
`DailyTask` without modifying `src` or the installed `ok-script` package:

```powershell
.\.venv\Scripts\python.exe -m extensions.cloud run-once
```

The bridge keeps synchronous Playwright on a dedicated owner thread. Existing
tasks receive normalized 1280×720 BGR frames and their keyboard/mouse calls are
dispatched back to that same thread. A custom one-time flow can still be supplied
with `--task-runner package.module:create_task_runner`.

Runs sharing a data directory also share an `execution.lock`. This prevents a
manual enrollment or `run-once` process from opening the persistent browser
profile while another cloud process is active. Scheduler state records the schedule identity and
the latest start, finish, status and return code; an unhandled worker exception
is recorded as failed without stopping the scheduler from serving later days.

`OK_WW_CLOUD_CONFIG_DIR` can redirect ok-script task settings without changing
the upstream project configuration. Docker sets it to `/data/cloud/configs`, so
task settings remain outside the image and persist with the cloud data volume.

Before running `DailyTask`, use the read-only preflight runner. It obtains a frame
through the real ok-script `TaskExecutor`, calculates basic image health metrics,
and runs OCR without logging recognized text. Its input guard rejects clicks,
keyboard input, scrolling, dragging and button presses:

```powershell
.\.venv\Scripts\python.exe -m extensions.cloud run-once `
  --task-runner extensions.cloud.preflight:create_preflight_task_runner
```

The preflight has a 180-second default timeout. Override it with
`OK_WW_CLOUD_PREFLIGHT_TIMEOUT`. Pointer movement is disabled by default; the
optional center-pointer probe can be enabled with
`OK_WW_CLOUD_PREFLIGHT_MOVE_POINTER=true`. The probe only moves the pointer and
never clicks, but it may slightly move the in-game camera when pointer lock is
active.

The Docker image installs a pinned Linux runtime for the complete task bridge:
headless OpenCV, OCR, OpenVINO and Playwright. PySide6 is intentionally omitted;
the cloud runner uses ok-script's headless core and does not instantiate the Qt
configuration tab. `ok-script==2.0.7b1` currently declares five native/host-input
packages that are unused by the Playwright bridge but cannot be installed safely
on Linux. The image therefore downloads the exact pure-Python wheel, checks its
fixed SHA-256, and creates a deterministic copy whose metadata removes only those
dependency declarations. It does not modify the source tree or host site-packages.
Because Python executes a package initializer before a requested submodule, the
copy also reduces the two device-backend aggregate initializers to their neutral
base exports; otherwise an import of `capture_methods.base` still imports all
Win32 backends. All other upstream source remains unchanged. The build runs
`pip check` and imports every configured one-time task, trigger task, scene,
OCR/OpenVINO stack and the default DailyTask factory. This compatibility step is
temporary and should be removed when upstream publishes platform-safe aggregates
and dependency markers.

## State evidence policy

- Prefer user-visible text and accessible roles over generated CSS classes.
- Treat the login iframe URL as supporting evidence, not the only login test.
- Require at least two signals before declaring the game active, such as
  `window.GameRunning` and a changing stream frame.
- Keep queue type explicit. Normal queue is the default; fast queue must be
  opted into because it may consume paid time.
- Never call private signed endpoints directly. Drive the public page and use
  network events only for diagnostics.
- Always release held input and close the browser context in a `finally` path.
  The cloud flow intentionally does not use the in-game exit control.

For installation and operations, see the Chinese
[Docker Compose deployment guide](zh-CN/docker-cloud.md). This document focuses
on implementation and verification evidence.

## Verification status and remaining work

Persistent-profile recovery, queue entry, non-black 1280×720 capture, owner-thread
input dispatch and the full `DailyTask` have been verified on the real cloud-game
page. The end-to-end run covered guide navigation, challenge selection, sustained
combat input, stamina reward collection, daily reward collection, mail, battle pass,
and the weekly activity target before returning to the open world. Docker
Xvfb/noVNC has also completed the same real-account flow at 1280×720,
including reward collection and a clean process exit. The remaining work is:

1. Add repeat-run measurements for screenshot throughput and long-session stability.
2. Measure whether GPU-assisted operation is required on slower hosts or under
   prolonged stream load.

The Docker path remains experimental while these repeatability and performance
measurements are collected, even though the functional end-to-end gate has passed.
