from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ok.task.web import call_task_tab_operation, task_tab_operations
from ok.util.config import Config

from extensions.cloud.character_code_task import CloudCharacterCodeTask
from src.char.Chixia import Chixia
from src.char.CustomCharLoader import clear_team_char_cache, list_custom_teams
from src.char.Mortefi import Mortefi
from src.char.Verina import Verina


class _Executor:
    def get_all_tasks(self):
        return []


class TestCloudCharacterCodeTask(unittest.TestCase):
    def setUp(self):
        self.old_config_folder = Config.config_folder
        self.temp_dir = tempfile.TemporaryDirectory()
        Config.config_folder = self.temp_dir.name
        clear_team_char_cache()
        self.task = object.__new__(CloudCharacterCodeTask)
        self.task._executor = _Executor()
        self.task._feature_index = {}
        self.task._image_cache = {}
        self.task.emit_web_event = Mock()
        self.team = [Mortefi.__name__, Chixia.__name__, Verina.__name__]

    def tearDown(self):
        clear_team_char_cache()
        Config.config_folder = self.old_config_folder
        self.temp_dir.cleanup()

    def test_declares_team_web_tab_and_allowlisted_operations(self):
        self.assertEqual(CloudCharacterCodeTask.web_tab.id, "character-code")
        self.assertTrue(CloudCharacterCodeTask.web_tab.resolved_entrypoint.is_file())
        operations = task_tab_operations(self.task)
        self.assertIn(("query", "state"), operations)
        self.assertIn(("query", "member"), operations)
        for name in (
            "create-team", "delete-team", "save-member", "reset-member",
            "export-team", "import-team",
        ):
            self.assertIn(("action", name), operations)

    def test_create_edit_reset_and_delete_team(self):
        created = call_task_tab_operation(
            self.task, "action", "create-team", {"members": self.team}
        )
        self.assertEqual(len(list_custom_teams()), 1)
        state = call_task_tab_operation(self.task, "query", "state")
        self.assertEqual(state["teams"][0]["key"], created["key"])

        member = call_task_tab_operation(
            self.task,
            "query",
            "member",
            {"team": self.team, "class_name": Mortefi.__name__},
        )
        custom_code = member["code"] + "\n# browser team code\n"
        saved = call_task_tab_operation(
            self.task,
            "action",
            "save-member",
            {"team": self.team, "class_name": Mortefi.__name__, "code": custom_code},
        )
        self.assertEqual(saved["code"], custom_code)
        self.assertFalse(saved["is_builtin"])

        reset = call_task_tab_operation(
            self.task,
            "action",
            "reset-member",
            {"team": self.team, "class_name": Mortefi.__name__},
        )
        self.assertTrue(reset["is_builtin"])

        call_task_tab_operation(
            self.task, "action", "delete-team", {"team": self.team}
        )
        self.assertEqual(list_custom_teams(), [])

    def test_export_and_import_archive_round_trip(self):
        created = call_task_tab_operation(
            self.task, "action", "create-team", {"members": self.team}
        )
        exported = call_task_tab_operation(
            self.task,
            "action",
            "export-team",
            {
                "team": self.team,
                "name": "Browser Team",
                "description": "Browser export",
                "author": "Tester",
                "version": "1.0.0",
            },
        )
        self.assertEqual(exported["filename"], "Browser_Team_Tester_1.0.0.zip")
        self.assertGreater(len(exported["content_base64"]), 100)

        call_task_tab_operation(
            self.task, "action", "delete-team", {"team": self.team}
        )
        imported = call_task_tab_operation(
            self.task,
            "action",
            "import-team",
            {"content_base64": exported["content_base64"]},
        )
        self.assertEqual(imported["key"], created["key"])
        self.assertEqual(imported["manifest"]["author"], "Tester")
        self.assertEqual(len(list_custom_teams()), 1)

    def test_rejects_duplicate_or_unknown_team_members(self):
        with self.assertRaisesRegex(ValueError, "3 different"):
            call_task_tab_operation(
                self.task,
                "action",
                "create-team",
                {"members": [Mortefi.__name__, Mortefi.__name__, Verina.__name__]},
            )
        with self.assertRaisesRegex(ValueError, "Unknown character"):
            call_task_tab_operation(
                self.task,
                "action",
                "create-team",
                {"members": [Mortefi.__name__, Chixia.__name__, "NotACharacter"]},
            )

    def test_frontend_contains_desktop_core_controls(self):
        script = Path(CloudCharacterCodeTask.web_tab.resolved_entrypoint).read_text(encoding="utf-8")
        for operation in (
            '"create-team"', '"delete-team"', '"save-member"',
            '"reset-member"', '"export-team"', '"import-team"',
        ):
            self.assertIn(operation, script)
        self.assertIn("registerSave", script)
        self.assertIn("Discard unsaved character code changes?", script)


if __name__ == "__main__":
    unittest.main()
