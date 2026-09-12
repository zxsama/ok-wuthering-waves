"""Browser-only DailyTask adaptations without changing upstream task identity."""

from __future__ import annotations

import re
from types import MethodType
from typing import Any


_BATTLE_PASS_TITLE = re.compile(r"先[约約][电電]台|Pioneer\s+Podcast", re.IGNORECASE)
_NUMBER = re.compile(r"\d+")


def claim_battle_pass(task: Any) -> None:
    """Enter through the terminal; the streamed HUD Alt-click is unreliable."""

    task.info_set("current task", "claim battle pass")
    task.log_info("battle pass (cloud)")
    task.ensure_main()
    task.send_key("esc")
    entry = task.wait_ocr(
        0.4, 0.12, 0.95, 0.4,
        match=_BATTLE_PASS_TITLE, time_out=10, settle_time=0.3,
        raise_if_not_found=True,
    )
    task.click_box(entry, after_sleep=1)
    task.wait_ocr(
        0.02, 0.02, 0.2, 0.09,
        match=_BATTLE_PASS_TITLE, time_out=10, settle_time=0.3,
        raise_if_not_found=True,
    )
    task.wait_ocr(
        0.2, 0.13, 0.32, 0.22,
        match=_NUMBER, settle_time=1, raise_if_not_found=True,
    )

    # Keep the upstream reward-tab sequence here. Only the cloud task instance
    # gets this method, so desktop input and persisted task identifiers survive.
    task.click_relative(0.04, 0.3, after_sleep=1)
    task.click_relative(0.68, 0.91, hcenter=True, after_sleep=3)
    task.click_relative(0.04, 0.17, after_sleep=2)
    task.click_relative(0.68, 0.91, hcenter=True, after_sleep=2)
    task.wait_ocr(
        0.2, 0.13, 0.32, 0.22,
        match=_NUMBER,
        post_action=lambda: task.click(0.68, 0.91, after_sleep=1),
        settle_time=1, raise_if_not_found=True,
    )
    task.ensure_main()


def install_daily_task_support(executor: Any) -> None:
    """Bind after CloudOK creates its tasks, preserving class/config identity.

    Avoid global monkey-patches and replacement task classes: multi-account
    tasks and saved schedules still refer to src.task.DailyTask.DailyTask.
    Custom subclasses retain their own behavior.
    """

    from src.task.DailyTask import DailyTask

    for task in executor.onetime_tasks:
        if type(task) is DailyTask:
            task.claim_battle_pass = MethodType(claim_battle_pass, task)
