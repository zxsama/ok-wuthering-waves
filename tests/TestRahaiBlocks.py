import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from src.task.rahai_blocks import (
    Board, COLS, ROWS, Piece, canonical_frame, choose_move, clear_lines, placements,
    preview_matches, read_board, score_bar_full,
)


def fixture(name):
    path = Path(__file__).with_name('images') / f'rahai_{name}.jpg'
    return canonical_frame(cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR))


class TestRahaiBlocks(unittest.TestCase):
    def test_intersecting_lines_clear_simultaneously(self):
        self.assertEqual(clear_lines(ROWS[2] | COLS[3] | 1), (1, 2))

    def test_full_lines_include_but_do_not_remove_gray_blocks(self):
        corners = 0x8100000000000081
        mask = ROWS[0] | COLS[0] | corners | (1 << 9)
        self.assertEqual(clear_lines(mask, corners), (corners | (1 << 9), 2))

    def test_rotations_cannot_mirror_or_wrap(self):
        moves = placements(((0, 0), (0, 1), (1, 1)))
        self.assertEqual({turns for turns, *_ in moves}, {0, 1, 2, 3})
        for _, row, col, shape, mask in moves:
            self.assertEqual(mask.bit_count(), len(shape))
            self.assertTrue(all(row + y < 8 and col + x < 8 for x, y in shape))

    def test_solver_considers_the_other_piece(self):
        square = Piece(0, ((0, 0), (0, 1), (1, 0), (1, 1)), (0, 0))
        single = Piece(1, ((0, 0),), (0, 0))
        row = ROWS[1] ^ (1 << 8)
        almost_full = row | ROWS[0] ^ 3
        board = Board(almost_full, (square, single))
        move = choose_move(board)
        self.assertEqual(move.slot, 1)
        self.assertFalse(move.mask & board.mask)
        self.assertEqual(clear_lines(board.mask | move.mask)[1], 1)

    def test_reads_real_board_and_ignores_rotation_tooltip(self):
        board = read_board(fixture('score'))
        self.assertEqual(board.mask, 27026459673497112)
        self.assertEqual([len(p.cells) for p in board.pieces], [4, 5])
        move = choose_move(board)
        self.assertEqual((move.slot, move.row, move.col), (0, 2, 5))
        self.assertTrue(preview_matches(fixture('preview'), board, move))
        self.assertFalse(preview_matches(fixture('score'), board, move))

    def test_reward_overlay_is_not_playable_and_has_full_bar(self):
        for board_name, reward_name in (('score', 'reward'), ('score_4500', 'reward_4500')):
            with self.subTest(board=board_name):
                reward = fixture(reward_name)
                self.assertIsNone(read_board(reward))
                self.assertTrue(score_bar_full(reward))
                self.assertFalse(score_bar_full(fixture(board_name)))

    def test_reads_three_trays_and_all_four_gray_corners(self):
        board = read_board(fixture('score_4500'))
        self.assertEqual(board.mask, 0x991800C3C3001899)
        self.assertEqual(board.fixed, 0x8100000000000081)
        self.assertEqual([(p.slot, len(p.cells)) for p in board.pieces], [(0, 5), (1, 4), (2, 5)])
        move = choose_move(board)
        self.assertFalse(move.mask & board.mask)
        alternative = choose_move(board, excluded={(move.slot, move.mask)})
        self.assertNotEqual((alternative.slot, alternative.mask), (move.slot, move.mask))

    def test_reads_medal_board_that_matches_both_tray_geometries(self):
        board = read_board(fixture('medal_start'))
        self.assertEqual(board.mask, 0x800E7AC2DEE8C12)
        self.assertEqual(board.fixed, 0x800008001000010)
        self.assertEqual([(p.slot, len(p.cells)) for p in board.pieces], [(0, 4), (1, 4), (2, 5)])
        self.assertEqual(board.pieces[2].cells, tuple((x, 0) for x in range(5)))

    def test_partly_used_three_tray_layout_keeps_slot_ids(self):
        board = read_board(fixture('score_4500_partial'))
        self.assertEqual(board.mask, 0x9B1A3E0E3E001A9B)
        self.assertEqual(board.fixed, 0x8100000000000081)
        self.assertEqual([p.slot for p in board.pieces], [0, 1])

    def test_bright_piece_does_not_look_like_two_tray_divider(self):
        board = read_board(fixture('score_4500_ambiguous_trays'))
        self.assertIsNotNone(board)
        self.assertEqual([(piece.slot, len(piece.cells)) for piece in board.pieces], [(0, 9), (1, 4), (2, 5)])

    def test_unrelated_or_empty_frames_are_rejected(self):
        self.assertIsNone(read_board(canonical_frame(np.zeros((720, 1280, 3), np.uint8))))
        self.assertIsNone(canonical_frame(np.empty((0, 1280, 3), np.uint8)))
        self.assertIsNone(canonical_frame(None))

    def test_scaled_event_board_and_drop_preview_at_different_aspect_ratios(self):
        from src.task.RahaiBlocksTask import RahaiBlocksTask

        original = fixture('score')
        preview = fixture('preview')
        for size in ((1600, 1200), (1920, 800)):
            with self.subTest(size=size):
                game_frame = cv2.resize(original, size)
                board = read_board(canonical_frame(game_frame))
                self.assertEqual(board.mask, 27026459673497112)
                move = choose_move(board)
                self.assertEqual((move.slot, move.row, move.col), (0, 2, 5))
                task = Mock(frame=game_frame)
                task.next_frame.return_value = cv2.resize(preview, size)
                interaction = Mock()
                self.assertTrue(RahaiBlocksTask._place(task, interaction, board, move))
                x, y = board.pieces[0].pickup
                interaction.mouse_down.assert_called_once_with(
                    round(x * size[0] / original.shape[1]), round(y * size[1] / original.shape[0])
                )
                interaction.mouse_up.assert_called_once_with()

    def test_task_scans_with_browser_drag_input_without_a_window(self):
        from src.task.RahaiBlocksTask import RahaiBlocksTask

        drag = Mock()
        manager = SimpleNamespace(drag_interaction=drag, hwnd_window=None)
        task = Mock(executor=SimpleNamespace(device_manager=manager))
        stage = [(Mock(), SimpleNamespace(name='已通关'))]
        task.wait_until.return_value = stage
        task._first_page.return_value = stage
        task._scroll_stages.return_value = (stage, False)
        RahaiBlocksTask.run(task)
        task._scroll_stages.assert_called_once_with(drag, 1, stage)

    def test_drag_releases_button_when_session_loses_focus(self):
        from src.task.RahaiBlocksTask import RahaiBlocksTask

        frame = fixture('score')
        board = read_board(frame)
        interaction = Mock()
        interaction.clickable.side_effect = [True, False, False]
        with self.assertRaisesRegex(RuntimeError, 'drag'):
            RahaiBlocksTask._place(Mock(frame=frame), interaction, board, choose_move(board))
        interaction.mouse_up.assert_called_once_with()
