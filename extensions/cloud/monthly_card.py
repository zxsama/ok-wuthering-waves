"""Check the contrast of monthly-card matches in streamed game frames."""

from functools import wraps

import cv2


def install_monthly_card_check(feature_set):
    find_one_feature = feature_set.find_one_feature

    @wraps(find_one_feature)
    def find_with_contrast(mat, category_name, *args, **kwargs):
        matches = find_one_feature(mat, category_name, *args, **kwargs)
        if category_name != "monthly_card" or not matches:
            return matches
        # Normalized correlation can match compression noise on a flat daily
        # activity row. The card itself has a dark ring on a bright background.
        return [box for box in matches
                if cv2.cvtColor(box.crop_frame(mat), cv2.COLOR_BGR2GRAY).std() >= 15]

    feature_set.find_one_feature = find_with_contrast
