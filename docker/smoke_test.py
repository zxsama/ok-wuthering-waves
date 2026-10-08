"""Build-time imports for the Linux cloud-task image."""

from __future__ import annotations

import importlib.metadata
import importlib

import cv2
import fastapi
import numpy
import onnxocr
import openvino
import uvicorn
from ok import OK, windows_graphics_available
from ok.core.start_controller import StartController
from ok.device.capture_methods.base import BaseCaptureMethod
from ok.device.interaction_methods.base import BaseInteraction
from ok.notification import NotificationManager
from ok.task.TaskExecutor import TaskExecutor

from config import config
from docker.playwright_adapter import create_playwright_adapter
from extensions.cloud.task_runner import create_daily_task_runner
from extensions.cloud.web_service import create_cloud_web_app
from src.task.DailyTask import DailyTask
from src.task.MouseResetTask import MouseResetTask


assert importlib.metadata.version("ok-script") == "2.0.8"
assert cv2.__version__
assert numpy.__version__
assert openvino.__version__
assert onnxocr
assert fastapi and uvicorn and callable(create_cloud_web_app)
assert (
    OK
    and BaseCaptureMethod
    and BaseInteraction
    and TaskExecutor
    and StartController
    and NotificationManager
)
assert windows_graphics_available() is False
assert config["ocr"]["params"]["use_openvino"] is True
assert DailyTask and MouseResetTask
assert callable(create_daily_task_runner())
assert create_playwright_adapter().ignore_default_args == ()

# CloudOK initializes every configured task before selecting DailyTask.  Import
# the same module list here so a platform-specific top-level import breaks the
# image build instead of the first scheduled run.
for section in ("onetime_tasks", "trigger_tasks"):
    for module_name, class_name in config[section]:
        assert getattr(importlib.import_module(module_name), class_name)
scene_module, scene_class = config["scene"]
assert getattr(importlib.import_module(scene_module), scene_class)
