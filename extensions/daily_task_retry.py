"""Retry the daily task without changing its game-specific steps."""

from functools import wraps
from typing import Any

from ok.task.exceptions import FinishedException, TaskDisabledException


RETRY_COUNT = "Daily Task Failure Retries"
DEFAULT_RETRY_COUNT = 5
RETRY_DELAY_SECONDS = 5


def install_daily_task_retry(task: Any) -> None:
    """Install before load_config so saved retry settings survive validation."""
    if getattr(task, "_daily_task_retry_installed", False):
        return

    task.default_config[RETRY_COUNT] = DEFAULT_RETRY_COUNT
    task.config_description[RETRY_COUNT] = (
        "Additional attempts after Daily Task fails. 0 disables retries. "
        "Each retry restarts the daily task after 5 seconds."
    )
    task.config_type[RETRY_COUNT] = {"min": 0}
    original_validate = task.validate_config
    original_run = task.run

    @wraps(original_validate)
    def validate_config(key, value):
        if key == RETRY_COUNT and (type(value) is not int or value < 0):
            return "Daily Task Failure Retries must be a non-negative integer."
        return original_validate(key, value)

    def check_stopped():
        if task.exit_is_set():
            raise FinishedException()
        if not task.enabled:
            raise TaskDisabledException()

    @wraps(original_run)
    def run(*args, **kwargs):
        retries = task.config.get(RETRY_COUNT, DEFAULT_RETRY_COUNT)
        error = validate_config(RETRY_COUNT, retries)
        if error:
            raise ValueError(error)
        for attempt in range(retries + 1):
            try:
                return original_run(*args, **kwargs)
            except (TaskDisabledException, FinishedException):
                raise
            except Exception as exc:
                if attempt == retries:
                    raise
                check_stopped()
                task.log_warning(
                    f"Daily Task failed; retry {attempt + 1}/{retries} "
                    f"in {RETRY_DELAY_SECONDS} seconds: {exc}"
                )
                # TaskExecutor.sleep normally captures frames and calls
                # sleep_check. A transient capture/task failure must not
                # interrupt this recovery delay. Keep its stop/pause handling.
                sleep_check_interval = task.sleep_check_interval
                task.sleep_check_interval = -1
                try:
                    task.sleep(RETRY_DELAY_SECONDS)
                finally:
                    task.sleep_check_interval = sleep_check_interval
                check_stopped()

    task.validate_config = validate_config
    task.run = run
    task._daily_task_retry_installed = True
