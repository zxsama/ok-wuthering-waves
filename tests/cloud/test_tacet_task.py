from types import MethodType, SimpleNamespace
from unittest.mock import Mock, call

import pytest
from ok.task.exceptions import CannotFindException, TaskDisabledException

from extensions.cloud.ok_bridge import create_cloud_ok_class
from extensions.cloud.tacet_task import install_tacet_task_support, walk_to_treasure
from src.task.BaseWWTask import BaseWWTask
from src.task.TacetTask import TacetTask


def approach_scenario(monkeypatch, prompts):
    task = Mock()
    task.wait_until.side_effect = prompts
    upstream_approach = Mock()
    monkeypatch.setattr(BaseWWTask, "walk_to_treasure", upstream_approach)
    return task, upstream_approach


@pytest.mark.parametrize("misses", [0, 1, 2, 9])
def test_lost_prompt_repositions_then_returns_when_f_is_visible(monkeypatch, misses):
    task, approach = approach_scenario(monkeypatch, [None] * misses + [object()])

    walk_to_treasure(task)

    assert approach.call_args_list == [
        call(task, send_f=False, raise_if_not_found=True)
    ] * (misses + 1)
    assert task.wait_until.call_args_list == [
        call(task.find_f_with_text, time_out=5, raise_if_not_found=False)
    ] * (misses + 1)
    assert task.log_info.call_count == misses
    task.walk_until_f.assert_not_called()
    task.send_key_down.assert_not_called()
    task.send_key.assert_not_called()


def test_all_missing_prompts_stop_after_ten_approaches(monkeypatch):
    task, approach = approach_scenario(monkeypatch, [None] * 10)

    with pytest.raises(CannotFindException, match="after 10 cloud approaches"):
        walk_to_treasure(task)

    assert approach.call_count == 10
    assert task.wait_until.call_count == 10


def test_optional_prompt_failure_keeps_nonraising_behavior(monkeypatch):
    task, approach = approach_scenario(monkeypatch, [None] * 10)

    assert walk_to_treasure(task, raise_if_not_found=False) is None

    assert approach.call_count == 10
    task.log_warning.assert_called_once()


def test_navigation_only_keeps_upstream_behavior(monkeypatch):
    task, approach = approach_scenario(monkeypatch, [])

    walk_to_treasure(task, send_f=False)

    approach.assert_called_once_with(task, send_f=False, raise_if_not_found=True)
    task.wait_until.assert_not_called()


@pytest.mark.parametrize("stage", ["navigation", "prompt"])
def test_task_cancellation_propagates_without_retry(monkeypatch, stage):
    task, approach = approach_scenario(monkeypatch, [TaskDisabledException()])
    if stage == "navigation":
        approach.side_effect = TaskDisabledException()

    with pytest.raises(TaskDisabledException):
        walk_to_treasure(task)

    assert approach.call_count == 1
    task.log_info.assert_not_called()


def test_navigation_failure_is_not_mistaken_for_a_transient_prompt(monkeypatch):
    task, approach = approach_scenario(monkeypatch, [])
    approach.side_effect = RuntimeError("reward icon is missing")

    with pytest.raises(RuntimeError, match="reward icon is missing"):
        walk_to_treasure(task)

    assert approach.call_count == 1
    task.wait_until.assert_not_called()


def test_upstream_navigation_corrects_overshoot_before_retrying_prompt():
    task = Mock()
    task.walk_to_box = MethodType(BaseWWTask.walk_to_box, task)
    task.do_walk_to_box = MethodType(BaseWWTask.do_walk_to_box, task)
    task.width_of_screen.side_effect = lambda fraction: 1000 * fraction
    task.height_of_screen.side_effect = lambda fraction: 1000 * fraction
    task.find_one.return_value = None
    task.find_f_with_claim_text.side_effect = [None, object(), None, object()]
    task.find_treasure_icon.side_effect = [
        SimpleNamespace(center=lambda: (500, 300)),
        SimpleNamespace(center=lambda: (500, 800)),
    ]
    # First approach loses F after stopping. The icon then appears behind the
    # character, so the real upstream navigation must move back on its retry.
    task.wait_until.side_effect = [True, None, True, object()]

    walk_to_treasure(task)

    assert [
        entry for entry in task.mock_calls
        if entry[0] in {"send_key_down", "send_key_up"}
    ] == [
        call.send_key_down("w"), call.send_key_up("w"),
        call.send_key_down("s"), call.send_key_up("s"),
    ]
    assert task.wait_until.call_count == 4
    task.walk_until_f.assert_not_called()
    task.send_key.assert_not_called()


def test_install_preserves_native_and_custom_task_behavior():
    class CustomTacetTask(TacetTask):
        pass

    original = TacetTask.walk_to_treasure
    cloud = TacetTask.__new__(TacetTask)
    native = TacetTask.__new__(TacetTask)
    custom = CustomTacetTask.__new__(CustomTacetTask)
    executor = SimpleNamespace(onetime_tasks=[cloud, custom, object()])

    install_tacet_task_support(executor)
    install_tacet_task_support(executor)

    assert f"{type(cloud).__module__}.{type(cloud).__name__}" == "src.task.TacetTask.TacetTask"
    assert cloud.walk_to_treasure.__func__ is walk_to_treasure
    assert cloud.walk_to_treasure.__self__ is cloud
    assert native.walk_to_treasure.__func__ is original
    assert custom.walk_to_treasure.__func__ is original
    assert TacetTask.walk_to_treasure is original


def test_cloud_runtime_installs_recovery_on_registered_tacet_task(monkeypatch):
    from ok import OK

    task = TacetTask.__new__(TacetTask)
    executor = SimpleNamespace(onetime_tasks=[task])
    monkeypatch.setattr(OK, "__init__", lambda self, config: setattr(self, "task_executor", executor))

    runtime = create_cloud_ok_class(SimpleNamespace(capture_method=None))({})

    assert runtime.task_executor is executor
    assert task.walk_to_treasure.__func__ is walk_to_treasure
