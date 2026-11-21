"""Validate the observed effects of blasts and level-one faults on a line clear."""

from src.task.rahai_blocks import COLS, ROWS, SIZE, clear_lines


def blast_observation(board, move, expected, actual, fault_mask=0):
    """Return the observed board and tray if a line clear explains every change."""
    if not actual or actual.fixed != board.fixed or board.mask & move.mask:
        return None
    placed = board.mask | move.mask
    cleared, lines = clear_lines(placed, board.fixed)
    if not lines or cleared != expected:
        return None

    # Only occupied, non-gray tiles seen before the drop can have protection.
    faults = fault_mask & board.mask & ~board.fixed
    line_tiles = placed & ~expected
    thawed = line_tiles & faults
    after_line = expected | thawed
    if actual.mask & ~after_line:
        return None
    # Blasts resolve after the lines: newly thawed tiles can now disappear.
    protected = board.fixed | (faults & ~thawed)
    removed = after_line & ~actual.mask
    if removed & protected:
        return None
    if not thawed and (not removed or actual.mask == board.mask):
        return None

    # The tray must show that the piece was consumed. After the last one,
    # the game supplies a new batch of two or three pieces.
    remaining = tuple((p.slot, p.cells) for p in board.pieces if p.slot != move.slot)
    visible = tuple((p.slot, p.cells) for p in actual.pieces)
    if len(remaining) == len(board.pieces):
        return None
    if remaining:
        if visible != remaining:
            return None
    elif len(visible) not in (2, 3) or len({slot for slot, _ in visible}) != len(visible):
        return None

    # A protected tile cannot trigger a blast. Every unprotected neighbor
    # of each candidate center must actually disappear.
    centers = line_tiles & ~faults
    vulnerable = after_line & ~protected
    explained = 0
    while centers:
        center = centers & -centers
        centers ^= center
        row, col = divmod(center.bit_length() - 1, SIZE)
        rows = sum(ROWS[max(0, row - 1):row + 2])
        cols = sum(COLS[max(0, col - 1):col + 2])
        affected = vulnerable & rows & cols
        if not affected & ~removed:
            explained |= affected
    if explained == removed:
        return actual.mask, visible
    return None
