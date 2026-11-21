import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from ok.task.TaskExecutor import TaskExecutor

import config


class TestDisplayConfig(unittest.TestCase):

    def test_hud_reference_does_not_trigger_window_resize(self):
        display = config.config['supported_resolution']
        self.assertEqual(display['ratio'], '16:9')
        self.assertFalse(display.get('resize_to'))
        for width, height in ((1920, 1200), (2560, 1080)):
            with self.subTest(width=width, height=height):
                frame = np.zeros((height, width, 3), dtype=np.uint8)
                supported, _ = TaskExecutor.check_frame_and_resolution(
                    Mock(spec=TaskExecutor), display['ratio'], display.get('min_size'), frame=frame,
                )
                self.assertTrue(supported)


class TestCalculatePcExePath(unittest.TestCase):

    def test_none_path_uses_most_recently_run_executable(self):
        expected = r"C:\Games\Wuthering Waves.exe"

        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=expected) as find:
            with patch.object(config, "_find_pc_exe_from_registry") as find_registry:
                result = config.calculate_pc_exe_path(None)

        find.assert_called_once_with()
        find_registry.assert_not_called()
        self.assertEqual(expected, result)

    def test_none_path_falls_back_to_registry_lookup(self):
        expected = r"C:\Games\Wuthering Waves.exe"

        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=None):
            with patch.object(config, "_find_pc_exe_from_registry", return_value=expected) as find:
                result = config.calculate_pc_exe_path(None)

        find.assert_called_once_with()
        self.assertEqual(expected, result)

    def test_none_path_returns_none_when_no_installation_is_found(self):
        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=None):
            with patch.object(config, "_find_pc_exe_from_registry", return_value=None):
                result = config.calculate_pc_exe_path(None)

        self.assertIsNone(result)

    def test_running_path_still_derives_game_executable(self):
        running_path = (
            r"C:\Games\Wuthering Waves Game\Client\Binaries"
            r"\Win64\Client-Win64-Shipping.exe"
        )

        result = config.calculate_pc_exe_path(running_path)

        self.assertEqual(r"C:\Games\Wuthering Waves Game\Wuthering Waves.exe", result)

    def test_registered_launcher_path_finds_sibling_game_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp)
            launcher = install_root / "Wuthering Waves bilibili" / "launcher.exe"
            game_exe = install_root / "Wuthering Waves Game" / "Wuthering Waves.exe"
            launcher.parent.mkdir()
            game_exe.parent.mkdir()
            game_exe.touch()

            result = config._find_pc_exe_near_registered_path(str(launcher))

        self.assertEqual(str(game_exe), result)


if __name__ == "__main__":
    unittest.main()
