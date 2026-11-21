import unittest
from unittest.mock import Mock, patch

from extensions.rahai_blast import blast_observation
from src.task.RahaiBlocksTask import ChallengeFailed, RahaiBlocksTask
from src.task.rahai_blocks import Board, Move, Piece, ROWS, SIZE, choose_move, clear_lines


def tile(row, col):
    return 1 << (row * SIZE + col)


class TestRahaiBlast(unittest.TestCase):
    def setUp(self):
        self.piece = Piece(0, ((0, 0),), (100, 100))
        self.other = Piece(1, ((0, 0), (1, 0)), (100, 200))
        fixed = tile(3, 3) | tile(4, 5)
        adjacent = sum(tile(r, c) for r in (2, 4) for c in range(2, 7))
        added = tile(3, 4)
        self.move = Move(0, 0, 3, 4, self.piece.cells, added)
        self.before = Board((ROWS[3] ^ added) | adjacent | tile(0, 0),
                            (self.piece, self.other), fixed)
        self.expected, lines = clear_lines(self.before.mask | added, fixed)
        self.assertEqual(lines, 1)
        blast = sum(tile(r, c) for r in (2, 4) for c in (3, 4, 5))
        self.actual = Board((self.expected & ~blast) | fixed, (self.other,), fixed)

    def test_only_complete_neighborhoods_with_a_consumed_piece_match(self):
        a = self.actual

        def observe(actual):
            return blast_observation(self.before, self.move, self.expected, actual)

        self.assertIsNotNone(observe(a))
        invalid = (
            None,
            self.before,
            Board(self.expected, a.pieces, a.fixed),
            Board(a.mask | tile(2, 4), a.pieces, a.fixed),
            Board(a.mask & ~tile(0, 0), a.pieces, a.fixed),
            Board(a.mask | tile(0, 1), a.pieces, a.fixed),
            Board(a.mask & ~tile(4, 5), a.pieces, a.fixed),
            Board(a.mask, a.pieces, 0),
            Board(a.mask, self.before.pieces, a.fixed),
            Board(a.mask, (), a.fixed),
        )
        for actual in invalid:
            with self.subTest(actual=actual):
                self.assertIsNone(observe(actual))

    def test_last_piece_requires_a_refilled_tray(self):
        before = Board(self.before.mask, (self.piece,), self.before.fixed)
        for pieces, accepted in (((), False), ((self.piece,), False), ((self.piece, self.other), True)):
            with self.subTest(pieces=pieces):
                actual = Board(self.actual.mask, pieces, self.actual.fixed)
                observation = blast_observation(before, self.move, self.expected, actual)
                self.assertEqual(observation is not None, accepted)

    def test_corner_and_separate_blasts_do_not_wrap(self):
        added = tile(0, 0)
        move = Move(0, 0, 0, 0, self.piece.cells, added)
        extras = sum(tile(1, col) for col in (0, 1, 4, 6, 7)) | tile(2, 0)
        before = Board((ROWS[0] ^ added) | extras, (self.piece, self.other))
        expected, _ = clear_lines(before.mask | added)
        left_blast = tile(1, 0) | tile(1, 1)
        right_blast = tile(1, 6) | tile(1, 7)
        for removed, accepted in ((left_blast, True), (left_blast | right_blast, True),
                                  (left_blast | tile(1, 7), False), (left_blast | tile(2, 0), False)):
            with self.subTest(removed=removed):
                actual = Board(expected & ~removed, (self.other,))
                self.assertEqual(blast_observation(before, move, expected, actual) is not None, accepted)

    def test_replans_from_stable_blast_result(self):
        task = Mock(_rahai_blast_stage=True)
        task._playable_board.side_effect = (self.before, self.actual, None, None)
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        task._reward.return_value = True
        task._place.return_value = True
        second = choose_move(self.actual)
        expected, _ = clear_lines(self.actual.mask | second.mask, self.actual.fixed)
        task._read_board.side_effect = (self.before, self.actual, self.actual, Board(expected, (), self.actual.fixed))
        with patch("src.task.RahaiBlocksTask.choose_move", side_effect=(self.move, second)) as solver:
            RahaiBlocksTask._play_board(task, Mock())
        self.assertEqual([call.args[0] for call in solver.call_args_list], [self.before, self.actual])
        self.assertEqual(task._read_board.call_count, 4)

    def test_other_stages_and_transient_blast_frames_still_fail(self):
        for enabled, frames in ((False, [self.actual] * 4), (True, [self.actual, None] * 2)):
            with self.subTest(enabled=enabled):
                task = Mock(_rahai_blast_stage=enabled)
                task._playable_board.return_value = self.before
                task._read_board.side_effect = frames
                task._place.return_value = True
                task.wait_until.return_value = False
                with patch("src.task.RahaiBlocksTask.choose_move", return_value=self.move):
                    with self.assertRaisesRegex(ChallengeFailed, "落块后棋盘"):
                        RahaiBlocksTask._play_board(task, Mock())

    def test_failed_drag_never_enters_blast_confirmation(self):
        task = Mock(_rahai_blast_stage=True)
        task._playable_board.return_value = self.before
        task._place.return_value = False
        with patch("src.task.RahaiBlocksTask.choose_move", return_value=self.move):
            with self.assertRaisesRegex(ChallengeFailed, "落点预览"):
                RahaiBlocksTask._play_board(task, Mock())
        task._read_board.assert_not_called()

    def test_no_line_never_enters_blast_confirmation(self):
        task = Mock(_rahai_blast_stage=True)
        before = Board(tile(3, 3), (self.piece, self.other))
        task._playable_board.return_value = before
        task._place.return_value = True
        task._read_board.return_value = Board(0, (self.other,))
        with patch("src.task.RahaiBlocksTask.choose_move", return_value=self.move):
            with patch("extensions.rahai_blast.blast_observation") as observe:
                with self.assertRaisesRegex(ChallengeFailed, "落块后棋盘"):
                    RahaiBlocksTask._play_board(task, Mock())
        observe.assert_not_called()
