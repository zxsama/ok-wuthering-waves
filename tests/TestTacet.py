import unittest
from types import SimpleNamespace

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.task.BaseWWTask import BaseWWTask
from src.task.TacetTask import TacetTask

config['debug'] = True


class TestTacet(TaskTestCase):
    task_class = TacetTask
    config = config

    def test_find_treasure_icon(self):
        self.set_image('tests/images/treasure.png')
        treasure = self.task.find_treasure_icon()
        self.logger.info(f'find_treasure_icon1 {treasure}')
        self.assertIsNotNone(treasure)

        self.set_image('tests/images/treasure2.png')
        treasure = self.task.find_treasure_icon()
        self.logger.info(f'find_treasure_icon2 {treasure}')
        self.assertIsNotNone(treasure)

    def test_missing_treasure_returns_false_instead_of_aborting_task(self):
        calls = []
        task = SimpleNamespace(
            wait_until=lambda condition, **kwargs: calls.append(kwargs) or None,
        )

        result = BaseWWTask.do_walk_to_box(
            task,
            find_function=lambda: None,
            end_condition=lambda: False,
            time_out=30,
        )

        self.assertFalse(result)
        self.assertEqual(calls, [{"raise_if_not_found": False, "time_out": 30}])

    def test_walk_to_treasure_uses_fallback_text_after_first_search_fails(self):
        claim_text = object()
        fallback_text = object()
        calls = []
        task = SimpleNamespace(
            log_info=lambda message: None,
            find_treasure_icon=object(),
            find_f_with_claim_text=claim_text,
            find_f_with_text=fallback_text,
            walk_to_box=lambda find, end_condition: calls.append(end_condition)
            or end_condition is fallback_text,
            sleep=lambda seconds: None,
        )

        BaseWWTask.walk_to_treasure(task, send_f=False)

        self.assertEqual(calls, [claim_text, fallback_text])

if __name__ == '__main__':
    unittest.main()
