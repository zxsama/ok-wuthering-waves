import json

from docker.select_cloud_command import select_command


def test_missing_or_invalid_marker_selects_enroll(tmp_path):
    assert select_command(str(tmp_path), "https://cloud.invalid/") == "enroll"
    (tmp_path / "enrollment.json").write_text("invalid", encoding="utf-8")
    assert select_command(str(tmp_path), "https://cloud.invalid/") == "enroll"


def test_matching_enrollment_selects_serve(tmp_path):
    (tmp_path / "enrollment.json").write_text(
        json.dumps({"enrolled": True, "cloud_url": "https://cloud.invalid/"}),
        encoding="utf-8",
    )
    assert select_command(str(tmp_path), "https://cloud.invalid/") == "serve"


def test_marker_for_another_site_selects_enroll(tmp_path):
    (tmp_path / "enrollment.json").write_text(
        json.dumps({"enrolled": True, "cloud_url": "https://other.invalid/"}),
        encoding="utf-8",
    )
    assert select_command(str(tmp_path), "https://cloud.invalid/") == "enroll"
