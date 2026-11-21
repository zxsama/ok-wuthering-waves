import unittest

from extensions.rahai_blast import blast_observation
from src.task.rahai_blocks import Board, COLS, Move, Piece, ROWS, SIZE, clear_lines


def tile(row, col):
    return 1 << (row * SIZE + col)


class TestRahaiFaultRules(unittest.TestCase):
    def setUp(self):
        self.piece = Piece(0, ((0, 0),), (100, 100))
        self.other = Piece(1, ((0, 0), (1, 0)), (100, 200))
        self.move = Move(0, 0, 3, 7, self.piece.cells, tile(3, 7))
        fixed = tile(4, 3)
        neighbors = sum(tile(2, c) for c in range(1, 7))
        self.board = Board((ROWS[3] ^ self.move.mask) | neighbors | fixed,
                           (self.piece, self.other), fixed)
        self.expected, _ = clear_lines(self.board.mask | self.move.mask, fixed)
        self.center = tile(3, 3)
        self.near = tile(2, 3)
        self.burst = tile(2, 2) | self.near | tile(2, 4)

    def observe(self, mask, faults, pieces=None):
        actual = Board(mask, (self.other,) if pieces is None else pieces, self.board.fixed)
        return blast_observation(self.board, self.move, self.expected, actual, faults)

    def test_direct_thaw_requires_known_faults_and_explained_losses(self):
        faults = self.center | tile(3, 5)
        mask = self.expected | faults
        signature = self.observe(mask, faults)
        self.assertEqual(signature, (mask, ((self.other.slot, self.other.cells),)))
        shifted = Piece(self.other.slot, self.other.cells, (110, 209))
        self.assertEqual(self.observe(mask, faults, (shifted,)), signature)
        for unexpected in (mask ^ self.center, mask | tile(3, 4),
                           mask | self.move.mask, mask | tile(5, 5)):
            with self.subTest(unexpected=unexpected):
                self.assertIsNone(self.observe(unexpected, faults | self.move.mask | tile(5, 5)))

    def test_blast_thaws_neighbor_while_removing_other_neighbors(self):
        direct = tile(3, 0)
        mask = self.expected & ~self.burst | self.near
        self.assertIsNotNone(self.observe(mask, self.near))
        self.assertIsNotNone(self.observe(mask | direct, self.near | direct))
        self.assertIsNone(self.observe(mask | direct, direct))
        self.assertIsNone(self.observe(mask & ~self.near, self.near))
        self.assertIsNone(self.observe(mask & ~self.board.fixed, self.near))
        self.assertIsNone(self.observe(mask | direct | tile(5, 5), self.near | direct))

    def test_protected_center_cannot_detonate(self):
        self.assertIsNotNone(self.observe(self.expected & ~self.burst, 0))
        self.assertIsNone(self.observe(self.expected & ~self.burst | self.center,
                                       self.center))

    def test_intersecting_lines_can_thaw_then_blast_separate_faults(self):
        added = tile(3, 7)
        board = Board((ROWS[3] | COLS[7]) ^ added, self.board.pieces)
        expected, lines = clear_lines(board.mask | added)
        self.assertEqual(lines, 2)
        faults = tile(3, 1) | tile(1, 7)
        for observed in (faults, tile(1, 7), tile(3, 1), 0, tile(1, 6)):
            with self.subTest(observed=observed):
                signature = blast_observation(board, self.move, expected,
                                              Board(observed, (self.other,)), faults)
                self.assertEqual(signature is not None, observed & ~faults == 0)

    def test_two_columns_thaw_four_tiles_then_blast_the_upper_pair(self):
        # Stage four, hard: these masks were read immediately before and after the drop.
        other = Piece(1, ((0, 1), (1, 1), (2, 0), (2, 1)), (100, 100))
        piece = Piece(2, tuple((x, y) for x in range(3) for y in range(2)), (100, 200))
        move = Move(2, 0, 5, 2, piece.cells, 0x1C1C0000000000)
        fixed = 0x1800008181000018
        board = Board(0x1B214399995E3F1A, (other, piece), fixed)
        expected, lines = clear_lines(board.mask | move.mask, fixed)
        self.assertEqual((expected, lines), (0x1B2547818146271A, 2))
        faults = 0x421818420000
        observed = 0x1B2547998142231A
        for mask, accepted in ((observed, True), (observed | tile(3, 3), False),
                               (observed ^ tile(4, 3), False), (observed ^ tile(7, 0), False),
                               (observed ^ tile(2, 1), False), (observed ^ tile(0, 3), False)):
            with self.subTest(mask=mask):
                actual = Board(mask, (other,), fixed)
                result = blast_observation(board, move, expected, actual, faults)
                self.assertEqual(result is not None, accepted)

    def test_thaw_requires_a_consumed_piece_and_a_real_line(self):
        mask = self.expected | self.center
        for pieces in (self.board.pieces, (self.piece,), ()):
            with self.subTest(pieces=pieces):
                self.assertIsNone(self.observe(mask, self.center, pieces))
        last = Board(self.board.mask, (self.piece,), self.board.fixed)
        for pieces, accepted in (((self.piece,), False), ((self.other, self.other), False),
                                 ((self.piece, self.other), True)):
            with self.subTest(refill=pieces):
                actual = Board(mask, pieces, self.board.fixed)
                self.assertEqual(blast_observation(last, self.move, self.expected,
                                                   actual, self.center) is not None, accepted)
        incomplete = Board(self.board.mask ^ tile(3, 1), self.board.pieces, self.board.fixed)
        actual = Board(mask, (self.other,), self.board.fixed)
        self.assertIsNone(blast_observation(incomplete, self.move, self.expected,
                                            actual, self.center))


if __name__ == '__main__':
    unittest.main()
