from extensions.cloud.cli import _build_run_once_command, build_parser, main
from extensions.cloud.config import CloudSettings
from extensions.cloud.models import CloudPageState

from tests.cloud.fakes import FakePageAdapter


def test_enroll_command_supports_injected_adapter(tmp_path):
    adapter = FakePageAdapter([CloudPageState.HOME])

    result = main(
        ["--data-dir", str(tmp_path), "enroll"],
        adapter_factory=lambda: adapter,
    )

    assert result == 0
    assert adapter.open_args["visible"] is True
    assert adapter.actions == ["close"]


def test_run_once_command_supports_injected_task_runner(tmp_path):
    adapter = FakePageAdapter([CloudPageState.IN_GAME])
    tasks = []

    result = main(
        ["--data-dir", str(tmp_path), "run-once", "--task-runner", "unused:factory"],
        adapter_factory=lambda: adapter,
        task_runner_factory=lambda: lambda: tasks.append("ran"),
    )

    assert result == 0
    assert tasks == ["ran"]
    assert adapter.actions == ["close"]


def test_schedule_command_builds_fresh_run_once_worker(tmp_path):
    args = build_parser().parse_args(
        [
            "--adapter",
            "package:adapter",
            "--data-dir",
            str(tmp_path),
            "--headless",
            "schedule",
            "--at",
            "04:00",
            "--timezone",
            "Asia/Shanghai",
            "--task-runner",
            "package:task",
        ]
    )

    command = _build_run_once_command(args, CloudSettings(data_dir=tmp_path))

    assert command[-3:] == ["run-once", "--task-runner", "package:task"]
    assert command[1:3] == ["-m", "extensions.cloud"]
    assert "package:adapter" in command
    assert "--headless" in command


def test_run_once_defaults_to_repository_daily_task_bridge():
    args = build_parser().parse_args(["run-once"])

    assert (
        args.task_runner
        == "extensions.cloud.task_runner:create_daily_task_runner"
    )


def test_serve_uses_uncommon_loopback_port_by_default():
    args = build_parser().parse_args(["serve"])

    assert args.host == "127.0.0.1"
    assert args.port == 17880


def test_serve_passes_persistent_adapter_to_web_service(tmp_path, monkeypatch):
    adapter = FakePageAdapter([CloudPageState.HOME])
    observed = {}

    def fake_serve(settings, received_adapter, *, host, port):
        observed.update(
            settings=settings,
            adapter=received_adapter,
            host=host,
            port=port,
        )

    monkeypatch.setattr("extensions.cloud.web_service.serve_cloud_web", fake_serve)

    result = main(
        [
            "--data-dir",
            str(tmp_path),
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            "17881",
        ],
        adapter_factory=lambda: adapter,
    )

    assert result == 0
    assert observed["adapter"] is adapter
    assert observed["settings"].data_dir == tmp_path
    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 17881


def test_schedule_command_does_not_open_browser_in_parent_process(tmp_path, monkeypatch):
    observed = {}

    class FakeScheduler:
        def __init__(self, schedule, worker, *, state_path, lock_path):
            observed.update(
                schedule=schedule,
                worker=worker,
                state_path=state_path,
                lock_path=lock_path,
            )

        def run_forever(self):
            observed["started"] = True

    monkeypatch.setattr("extensions.cloud.cli.DailyScheduler", FakeScheduler)

    result = main(
        [
            "--adapter",
            "package:adapter",
            "--data-dir",
            str(tmp_path),
            "schedule",
            "--at",
            "04:00",
            "--timezone",
            "Asia/Shanghai",
            "--task-runner",
            "package:task",
        ],
        adapter_factory=lambda: (_ for _ in ()).throw(AssertionError("browser opened")),
    )

    assert result == 0
    assert observed["started"] is True
    assert observed["state_path"] == tmp_path / "scheduler-state.json"
    assert observed["lock_path"] == tmp_path / "scheduler.lock"
