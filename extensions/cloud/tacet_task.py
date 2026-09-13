"""Cloud-only recovery when a delayed reward prompt disappears after walking."""

from __future__ import annotations

from types import MethodType
from typing import Any


_APPROACH_ATTEMPTS = 10
_PROMPT_TIMEOUT_SECONDS = 5


def walk_to_treasure(
    task: Any, send_f: bool = True, raise_if_not_found: bool = True
) -> None:
    from ok.task.exceptions import CannotFindException
    from src.task.BaseWWTask import BaseWWTask

    for attempt in range(1, _APPROACH_ATTEMPTS + 1):
        # Reuse upstream navigation and its movement-key release. Do not run
        # its final forward walk: delayed key-up can already have overshot F.
        BaseWWTask.walk_to_treasure(
            task, send_f=False, raise_if_not_found=raise_if_not_found
        )
        if not send_f:
            return
        if task.wait_until(
            task.find_f_with_text,
            time_out=_PROMPT_TIMEOUT_SECONDS,
            raise_if_not_found=False,
        ):
            # Leave the confirmed frame ready for farm_tacet's pick_f().
            return
        if attempt < _APPROACH_ATTEMPTS:
            task.log_info(
                f"cloud reward prompt lost; repositioning "
                f"({attempt + 1}/{_APPROACH_ATTEMPTS})"
            )

    message = f"cannot find reward F prompt after {_APPROACH_ATTEMPTS} cloud approaches"
    if raise_if_not_found:
        raise CannotFindException(message)
    task.log_warning(message)


def install_tacet_task_support(executor: Any) -> None:
    """Keep desktop/custom tasks and stable task identifiers unchanged."""
    from src.task.TacetTask import TacetTask

    for task in executor.onetime_tasks:
        if type(task) is TacetTask:
            task.walk_to_treasure = MethodType(walk_to_treasure, task)
