from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from extensions.cloud.daily_task import claim_battle_pass, install_daily_task_support
from extensions.cloud.ok_bridge import create_cloud_ok_class
from src.task.DailyTask import DailyTask


def make_task():
    task = Mock()
    entry = SimpleNamespace(name="先约电台", x=920, y=200)
    task.wait_ocr.side_effect = [[entry], ["先约电台"], ["450/1000"], ["450/1000"]]
    return task, entry


def test_claim_enters_recognized_terminal_item_before_touching_rewards():
    task, entry = make_task()

    claim_battle_pass(task)

    task.send_key.assert_called_once_with("esc")
    task.send_key_down.assert_not_called()
    task.click_box.assert_called_once_with([entry], after_sleep=1)
    calls = task.method_calls
    first_reward = next(i for i, call in enumerate(calls) if call[0] == "click_relative")
    assert sum(call[0] == "wait_ocr" for call in calls[:first_reward]) == 3
    assert all(call.kwargs["raise_if_not_found"] for call in task.wait_ocr.call_args_list)
    assert calls[-1][0] == "ensure_main"


@pytest.mark.parametrize("failure_stage", [0, 1, 2])
def test_missing_entry_or_page_never_clicks_reward_positions(failure_stage):
    task, _ = make_task()
    task.wait_ocr.side_effect = [["found"]] * failure_stage + [RuntimeError("page missing")]

    with pytest.raises(RuntimeError, match="page missing"):
        claim_battle_pass(task)

    task.click_relative.assert_not_called()


def test_reward_dialog_failure_is_reported_instead_of_finishing_silently():
    task, _ = make_task()
    task.wait_ocr.side_effect = [["entry"], ["title"], ["450/1000"], RuntimeError("dialog stuck")]

    with pytest.raises(RuntimeError, match="dialog stuck"):
        claim_battle_pass(task)

    task.ensure_main.assert_called_once()


def test_install_preserves_identity_and_does_not_patch_native_or_custom_tasks():
    class CustomDailyTask(DailyTask):
        pass

    original = DailyTask.claim_battle_pass
    cloud = DailyTask.__new__(DailyTask)
    native = DailyTask.__new__(DailyTask)
    custom = CustomDailyTask.__new__(CustomDailyTask)
    executor = SimpleNamespace(onetime_tasks=[cloud, custom, object()])

    install_daily_task_support(executor)
    install_daily_task_support(executor)

    assert type(cloud) is DailyTask
    assert f"{type(cloud).__module__}.{type(cloud).__name__}" == "src.task.DailyTask.DailyTask"
    assert cloud.claim_battle_pass.__func__ is claim_battle_pass
    assert cloud.claim_battle_pass.__self__ is cloud
    assert native.claim_battle_pass.__func__ is original
    assert custom.claim_battle_pass.__func__ is original
    assert DailyTask.claim_battle_pass is original


def test_cloud_runtime_installs_support_after_tasks_are_created(monkeypatch):
    from ok import OK

    cloud_task = DailyTask.__new__(DailyTask)
    executor = SimpleNamespace(onetime_tasks=[cloud_task])
    monkeypatch.setattr(OK, "__init__", lambda self, config: setattr(self, "task_executor", executor))
    manager = SimpleNamespace(capture_method=None)

    runtime = create_cloud_ok_class(manager)({})

    assert runtime.task_executor is executor
    assert cloud_task.claim_battle_pass.__func__ is claim_battle_pass
