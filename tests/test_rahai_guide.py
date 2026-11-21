from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from extensions.rahai_challenge import GUIDE_TITLE, configure_stage_mechanics, dismiss_rahai_guide, return_to_stage_list
from src.task.RahaiBlocksTask import RahaiBlocksTask
from src.task.rahai_blocks import Board, Piece


def test_blast_guide_requires_title_and_confirmation():
    task = Mock()
    task.ocr.side_effect = [[], [SimpleNamespace(name="爆破方块")], ["确认"]]
    assert not dismiss_rahai_guide(task)
    task.click.assert_not_called()
    assert GUIDE_TITLE.search("爆破方块")
    assert dismiss_rahai_guide(task)
    assert task._rahai_blast_stage is True
    task.click.assert_called_once_with("确认", after_sleep=.5)


def test_fault_guide_and_returning_players():
    task = Mock()
    configure_stage_mechanics(task, "虚诞之地", "困难")
    assert task._rahai_blast_stage and task._rahai_fault_stage
    configure_stage_mechanics(task, "其他关卡", "简单")
    assert not task._rahai_blast_stage and not task._rahai_fault_stage
    task.ocr.side_effect = [[SimpleNamespace(name="1级故障方块")], ["确认"]]
    assert GUIDE_TITLE.search("1级故障方块") and dismiss_rahai_guide(task)
    assert task._rahai_fault_stage


def test_guide_can_appear_after_initial_board_detection():
    task = Mock()
    task._playable_board.return_value = None
    task._reward.return_value = False
    task.wait_until.return_value = Board(0, (Piece(0, ((0, 0),), (0, 0)),))
    # Stop at placement so this check also proves the board was recovered.
    interaction = Mock()
    task._place.side_effect = InterruptedError()
    with pytest.raises(InterruptedError):
        RahaiBlocksTask._play_board(task, interaction)
    task.wait_until.assert_called_once_with(task._ready_board, time_out=6)
    task._place.assert_called_once()


def test_return_via_event_overview_after_transient_list():
    task = Mock()
    final = ["第三关已完成"]
    task._stage_list.side_effect = [[], ["一帧列表"], [], [], [], final, final]
    task._stage_details.side_effect = [True]
    task.ocr.side_effect = [[], ["拉海洛方块·逆转"], ["立即前往"]]
    task.wait_until.side_effect = lambda observe, **kwargs: next(result for _ in range(6) if (result := observe()))
    assert return_to_stage_list(task) == final
    assert [call.args for call in task.click.call_args_list] == [(.942, .06), ("立即前往",)]
