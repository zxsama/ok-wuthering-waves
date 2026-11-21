from types import SimpleNamespace

import cv2
import numpy as np
from ok import OK
from ok.feature.FeatureSet import FeatureSet

from config import config
from extensions.cloud.ok_bridge import create_cloud_ok_class


def test_cloud_rejects_flat_card_matches_and_keeps_the_real_card(monkeypatch):
    features = FeatureSet(False, None, **config["template_matching"])
    original = cv2.imread("ok_templates/monthly_card.png")
    assert original is not None
    frame = cv2.resize(original, (1600, 900))
    # The daily activity list has nearly uniform gray rows. Compress the real
    # pattern into two gray levels to reproduce a spurious normalized match.
    flat = np.where(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) > 128, 221, 220).astype(np.uint8)
    flat = cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)

    def find(image):
        return features.find_feature(image, "monthly_card", threshold=0.65,
                                     horizontal_variance=0.05, vertical_variance=0.05, limit=1)

    assert find(flat)
    real_match = find(frame)[0]

    def init_ok(runtime, _config):
        runtime.feature_set = features
        runtime.task_executor = SimpleNamespace(onetime_tasks=[], feature_set=features)

    monkeypatch.setattr(OK, "__init__", init_ok)
    create_cloud_ok_class(SimpleNamespace(capture_method=None))({})

    assert not find(flat)
    assert find(frame)[0].confidence == real_match.confidence
