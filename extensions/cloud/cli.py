"""Command skeleton for enrollment and one-shot cloud runs."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .adapters import CloudPageAdapter
from .config import CloudSettings
from .models import CloudConfigurationError, CloudRunnerError
from .notifier import SmtpFailureNotifier, SmtpSettings
from .profile import ProfileStore
from .runner import CloudRunCoordinator
from .scheduler import DailySchedule, DailyScheduler, SubprocessWorker
from .session import CloudSession


def _load_factory(spec: str, purpose: str) -> Callable[[], Any]:
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise CloudConfigurationError(f"{purpose} must use module:factory syntax")
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
    except (ImportError, AttributeError) as exc:
        raise CloudConfigurationError(f"cannot load {purpose}: {spec}") from exc
    if not callable(factory):
        raise CloudConfigurationError(f"{purpose} is not callable: {spec}")
    return factory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m extensions.cloud")
    parser.add_argument(
        "--adapter",
        default=os.environ.get(
            "OK_WW_CLOUD_ADAPTER",
            "extensions.cloud.playwright_adapter:create_playwright_adapter",
        ),
        help="zero-argument browser adapter factory in module:factory form",
    )
    parser.add_argument("--data-dir", type=Path, help="persistent cloud-runner data directory")
    parser.add_argument("--headless", action="store_true", help="request a hidden browser")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("enroll", help="open a visible browser and wait for manual login")
    serve_parser = subparsers.add_parser(
        "serve", help="run the persistent browser configuration and scheduling service"
    )
    serve_parser.add_argument(
        "--host",
        default=os.environ.get("CLOUD_WEB_HOST", "127.0.0.1"),
        help="Web service bind address (default: CLOUD_WEB_HOST or 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CLOUD_WEB_PORT", "17880")),
        help="Web service port (default: CLOUD_WEB_PORT or 17880)",
    )
    run_parser = subparsers.add_parser("run-once", help="enter the game and run one task flow")
    run_parser.add_argument(
        "--task-runner",
        default=os.environ.get(
            "OK_WW_CLOUD_TASK_RUNNER",
            "extensions.cloud.task_runner:create_daily_task_runner",
        ),
        help="zero-argument factory returning a callable, in module:factory form",
    )
    schedule_parser = subparsers.add_parser(
        "schedule", help="run the cloud task once per day in a fresh worker process"
    )
    schedule_parser.add_argument(
        "--at",
        default=os.environ.get("RUN_AT", "04:00"),
        metavar="HH:MM",
        help="local daily run time (default: RUN_AT or 04:00)",
    )
    schedule_parser.add_argument(
        "--timezone",
        default=os.environ.get("TZ", "Asia/Shanghai"),
        help="IANA timezone name (default: TZ or Asia/Shanghai)",
    )
    schedule_parser.add_argument(
        "--missed-window-minutes",
        type=int,
        default=os.environ.get("MISSED_WINDOW_MINUTES", "120"),
        help="maximum late-start window (default: MISSED_WINDOW_MINUTES or 120)",
    )
    schedule_parser.add_argument(
        "--task-runner",
        default=os.environ.get(
            "OK_WW_CLOUD_TASK_RUNNER",
            "extensions.cloud.task_runner:create_daily_task_runner",
        ),
        help="task runner factory passed to each run-once worker",
    )
    return parser


def _build_run_once_command(args: argparse.Namespace, settings: CloudSettings) -> list[str]:
    command = [sys.executable, "-m", "extensions.cloud"]
    if args.adapter:
        command.extend(["--adapter", args.adapter])
    command.extend(["--data-dir", str(settings.data_dir)])
    if args.headless:
        command.append("--headless")
    command.extend(["run-once", "--task-runner", args.task_runner])
    return command


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter_factory: Callable[[], CloudPageAdapter] | None = None,
    task_runner_factory: Callable[[], Callable[[], None]] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = CloudSettings.from_env().with_overrides(
            data_dir=args.data_dir,
            browser_visible=False if args.headless else None,
        )
        if args.command == "schedule":
            if not args.adapter:
                raise CloudConfigurationError(
                    "a browser adapter is required; pass --adapter module:factory"
                )
            schedule = DailySchedule.create(
                args.at,
                args.timezone,
                args.missed_window_minutes,
            )
            scheduler = DailyScheduler(
                schedule,
                SubprocessWorker(_build_run_once_command(args, settings)),
                state_path=settings.data_dir / "scheduler-state.json",
                lock_path=settings.data_dir / "scheduler.lock",
            )
            try:
                scheduler.run_forever()
            except KeyboardInterrupt:
                pass
            return 0

        if adapter_factory is None:
            if not args.adapter:
                raise CloudConfigurationError(
                    "a browser adapter is required; pass --adapter module:factory"
                )
            adapter_factory = _load_factory(args.adapter, "browser adapter factory")

        profile = ProfileStore(settings.data_dir)
        adapter = adapter_factory()
        if args.command == "serve":
            from .web_service import serve_cloud_web

            serve_cloud_web(settings, adapter, host=args.host, port=args.port)
            return 0

        session = CloudSession(settings, profile, adapter)

        if args.command == "enroll":
            coordinator = CloudRunCoordinator(session, profile, lambda: None)
            coordinator.enroll()
        else:
            if task_runner_factory is None:
                task_runner_factory = _load_factory(args.task_runner, "task runner factory")
            smtp_settings = SmtpSettings.from_env()
            failure_notifier = (
                SmtpFailureNotifier(smtp_settings) if smtp_settings is not None else None
            )
            coordinator = CloudRunCoordinator(
                session,
                profile,
                task_runner_factory(),
                failure_notifier=failure_notifier,
            )
            coordinator.run_once()
        return 0
    except CloudRunnerError as exc:
        print(f"cloud runner failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
