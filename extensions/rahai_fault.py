"""Recognize the cracked overlay on level-one fault tiles in Rahai Blocks."""

import cv2

from src.task.rahai_blocks import BOARD_X, BOARD_Y, CELL, HEIGHT, SIZE, WIDTH


def fault_mask(frame):
    """Return the row-major bitmask of visible faults on a canonical BGR frame."""
    if frame is None or frame.shape != (HEIGHT, WIDTH, 3):
        return 0

    mask = 0
    for row in range(SIZE):
        y = round(BOARD_Y + (row + .5) * CELL)
        for col in range(SIZE):
            x = round(BOARD_X + (col + .5) * CELL)
            # Sample the tile face inside its rounded border. The center can
            # show a medal or a bomb on both ordinary and fault tiles.
            face = frame[y - 50:y + 50, x - 50:x + 50]
            small = cv2.resize(face, (8, 8), interpolation=cv2.INTER_AREA)
            light = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            # Three separate ridges in the overlay catch light. Each must
            # stand out from its neighboring shadow, even on a bright tile.
            top = light[0, 4:7].mean() - light[1:3, 7].mean()
            lower_right = light[6:, 7].mean() - light[5:7, 5:7].mean()
            left = light[:3, 0].mean() - light[4:6, 0].mean()
            if top >= 28 and lower_right >= 28 and left >= 17:
                mask |= 1 << (row * SIZE + col)
    return mask
