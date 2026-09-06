import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from src.combat.CombatCheck import CombatCheck
from src.task.WWOneTimeTask import WWOneTimeTask


class TestHeadlessImports(unittest.TestCase):

    def test_cloud_task_modules_import_without_win32(self):
        script = textwrap.dedent(
            """
            import sys
            from importlib.abc import MetaPathFinder

            class BlockWin32(MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in {'win32api', 'win32con', 'win32gui'}:
                        raise ModuleNotFoundError(f'blocked {fullname}')

            sys.meta_path.insert(0, BlockWin32())
            import src.task.WWOneTimeTask
            import src.combat.CombatCheck
            """
        )

        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestWWOneTimeTaskCompatibility(unittest.TestCase):

    def test_browser_run_skips_windows_cursor_and_activation_tasks(self):
        task = WWOneTimeTask()
        task.executor = MagicMock()
        task.is_browser = MagicMock(return_value=True)
        task.sleep = MagicMock()

        task.run()

        task.executor.get_task_by_class.assert_not_called()
        task.executor.interaction.activate.assert_not_called()
        task.sleep.assert_called_once_with(0.5)

    def test_native_run_keeps_mouse_reset_and_post_message_activation(self):
        class FakePostMessageInteraction:
            def activate(self):
                return None

        class FakeMouseResetTask:
            pass

        mouse_reset = MagicMock()
        task = WWOneTimeTask()
        task.executor = MagicMock()
        task.executor.interaction = FakePostMessageInteraction()
        task.executor.get_task_by_class.return_value = mouse_reset
        task.is_browser = MagicMock(return_value=False)
        task.sleep = MagicMock()
        mouse_reset_module = types.ModuleType('src.task.MouseResetTask')
        mouse_reset_module.MouseResetTask = FakeMouseResetTask

        import ok

        with patch.dict(ok.__dict__, {'PostMessageInteraction': FakePostMessageInteraction}), \
                patch.dict(sys.modules, {'src.task.MouseResetTask': mouse_reset_module}):
            with patch.object(task.executor.interaction, 'activate') as activate:
                task.run()

        task.executor.get_task_by_class.assert_called_once_with(FakeMouseResetTask)
        mouse_reset.run.assert_called_once_with()
        activate.assert_called_once_with()
        task.sleep.assert_called_once_with(0.5)


class TestCombatCheckCompatibility(unittest.TestCase):

    @staticmethod
    def make_levitator_task(*, browser):
        task = CombatCheck.__new__(CombatCheck)
        task.config = {'Check Levitator': True}
        task.key_config = {'Wheel Key': 'tab'}
        task._executor = MagicMock()
        task.find_one = MagicMock(return_value=None)
        task.has_char = MagicMock(return_value=False)
        task.is_open_world_auto_combat = MagicMock(return_value=False)
        task.is_browser = MagicMock(return_value=browser)
        task.sleep = MagicMock()
        task.in_team = MagicMock(return_value=(False, None))
        task.send_key_down = MagicMock()
        task.send_key_up = MagicMock()
        task.log_info = MagicMock()
        task.log_debug = MagicMock()
        task.box_of_screen = MagicMock(return_value=object())
        task.move = MagicMock()
        task.target_enemy = MagicMock(return_value=True)
        levitator = types.SimpleNamespace(x=320, y=240)

        def wait_feature(name, **kwargs):
            if name == 'wheel_levitator':
                return levitator
            if name == 'edge_levitator':
                return object()
            return None

        task.wait_feature = MagicMock(side_effect=wait_feature)
        return task, levitator

    def test_browser_levitator_uses_page_pointer_without_desktop_cursor(self):
        task, levitator = self.make_levitator_task(browser=True)

        self.assertTrue(task.ensure_levitator())

        task.move.assert_called_once_with(levitator.x, levitator.y)
        task.executor.interaction.capture.get_abs_cords.assert_not_called()
        task.send_key_up.assert_called_once_with('tab')

    def test_native_levitator_preserves_desktop_cursor(self):
        task, levitator = self.make_levitator_task(browser=False)
        win32api = MagicMock()
        win32api.GetCursorPos.return_value = (10, 20)
        task.executor.interaction.capture.get_abs_cords.return_value = (100, 200)

        with patch.dict(sys.modules, {'win32api': win32api}):
            self.assertTrue(task.ensure_levitator())

        task.executor.interaction.capture.get_abs_cords.assert_called_once_with(
            levitator.x, levitator.y
        )
        self.assertEqual(
            win32api.SetCursorPos.call_args_list,
            [call((100, 200)), call((10, 20))],
        )


if __name__ == '__main__':
    unittest.main()
