from pathlib import Path

import pytest

from extensions.cloud.config import CloudSettings
from extensions.cloud.models import CloudConfigurationError, QueueKind


def test_settings_load_from_environment():
    settings = CloudSettings.from_env(
        {
            "OK_WW_CLOUD_DATA_DIR": "runtime-data",
            "OK_WW_CLOUD_QUEUE": "fast",
            "OK_WW_CLOUD_BROWSER_VISIBLE": "false",
            "OK_WW_CLOUD_QUEUE_TIMEOUT": "30",
        }
    )

    assert settings.data_dir == Path("runtime-data")
    assert settings.queue == QueueKind.FAST
    assert settings.browser_visible is False
    assert settings.queue_timeout_seconds == 30


def test_settings_reject_invalid_queue():
    with pytest.raises(CloudConfigurationError):
        CloudSettings.from_env({"OK_WW_CLOUD_QUEUE": "unknown"})
