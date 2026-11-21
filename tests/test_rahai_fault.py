from pathlib import Path

import cv2
import numpy as np
import pytest

from extensions.rahai_fault import fault_mask
from src.task.rahai_blocks import BOARD_X, BOARD_Y, CELL, HEIGHT, WIDTH, canonical_frame


IMAGES = Path(__file__).with_name('images')


def load_image(name):
    return cv2.imdecode(np.fromfile(IMAGES / name, dtype=np.uint8), cv2.IMREAD_COLOR)


@pytest.mark.parametrize('name', [
    'medal_start', 'score', 'preview', 'score_4500', 'score_4500_partial',
    'score_4500_ambiguous_trays', 'reward', 'reward_4500',
])
def test_ordinary_boards_previews_and_results(name):
    assert fault_mask(canonical_frame(load_image(f'rahai_{name}.jpg'))) == 0


def test_only_cracked_medals_survive_as_faults():
    # Cropped faces from stage four: cracked purple/blue, normal purple/blue,
    # and a normal bomb. An arbitrary arrangement exercises the bit ordering.
    faces = np.hsplit(load_image('rahai_fault_tiles.png'), 5)
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for face, (row, col) in zip(faces, [(7, 7), (0, 2), (4, 6), (2, 1), (3, 3)]):
        x = round(BOARD_X + (col + .5) * CELL)
        y = round(BOARD_Y + (row + .5) * CELL)
        frame[y - 50:y + 50, x - 50:x + 50] = cv2.resize(face, (100, 100))
    assert fault_mask(frame) == (1 << 63) | (1 << 2)
