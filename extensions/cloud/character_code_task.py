from __future__ import annotations

import base64
import io
import json
import tempfile
from pathlib import Path

from ok import WebCustomTab, WebTabConfig, task_tab_action, task_tab_query
from PIL import Image

from src.char.CharFactory import apply_team_char_classes, char_dict
from src.char.CustomCharLoader import (
    create_custom_team,
    delete_custom_team,
    export_custom_team,
    get_english_char_name,
    import_custom_team,
    inspect_team_archive,
    list_custom_teams,
    normalize_team,
    read_builtin_char_code,
    read_team_char_code,
    save_team_char_code,
)


BASE_CHAR_URL = "https://raw.githubusercontent.com/ok-oldking/ok-wuthering-waves/refs/heads/master/src/char/BaseChar.py"
MAX_IMPORT_BYTES = 20_000_000


class CloudCharacterCodeTask(WebCustomTab):
    """Browser team/character-code editor backed by the desktop persistence API."""

    web_tab = WebTabConfig(
        id="character-code",
        name="Character Code",
        icon="code",
        asset_dir=Path(__file__).resolve().parent / "web" / "character_code",
        add_after_default_tabs=True,
        task_controls=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "Character Code"
        self._feature_index = None
        self._image_cache = {}

    def _characters(self):
        characters = {}
        labels = {}
        for label, info in char_dict.items():
            char_cls = info.get("cls")
            if char_cls is None:
                continue
            characters[char_cls.__name__] = char_cls
            labels.setdefault(char_cls.__name__, getattr(label, "value", str(label)))
        return characters, labels

    def _character_class(self, class_name):
        characters, _labels = self._characters()
        char_cls = characters.get(str(class_name or ""))
        if char_cls is None:
            raise ValueError(f"Unknown character: {class_name}")
        return char_cls

    def _team(self, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Team must be a list of 3 characters")
        team = normalize_team(value)
        characters, _labels = self._characters()
        unknown = next((name for name in team if name not in characters), None)
        if unknown:
            raise ValueError(f"Unknown character: {unknown}")
        return team

    def _team_payload(self, team):
        normalized = self._team(team)
        return {
            "key": "__".join(normalized),
            "members": list(normalized),
            "display_name": ", ".join(get_english_char_name(name) for name in normalized),
        }

    def _member_payload(self, team, class_name):
        normalized = self._team(team)
        if class_name not in normalized:
            raise ValueError(f"{class_name} is not in this team")
        char_cls = self._character_class(class_name)
        code = read_team_char_code(normalized, char_cls)
        builtin = read_builtin_char_code(char_cls)
        return {
            "team": list(normalized),
            "class_name": class_name,
            "display_name": get_english_char_name(class_name),
            "code": code,
            "builtin_code": builtin,
            "is_builtin": code == builtin,
            "image_data_url": self._character_image_data_url(char_cls),
            "base_char_url": BASE_CHAR_URL,
        }

    @task_tab_query("state")
    def state(self):
        characters, _labels = self._characters()
        options = [
            {
                "class_name": name,
                "display_name": get_english_char_name(name),
                "image_data_url": self._character_image_data_url(char_cls),
            }
            for name, char_cls in characters.items()
        ]
        options.sort(key=lambda item: item["display_name"].casefold())
        return {
            "characters": options,
            "teams": [self._team_payload(team) for team in list_custom_teams()],
        }

    @task_tab_query("member")
    def member(self, payload):
        team = self._team(payload.get("team"))
        return self._member_payload(team, str(payload.get("class_name") or ""))

    @task_tab_action("create-team")
    def create_team(self, payload):
        team = self._team(payload.get("members"))
        create_custom_team(team)
        result = self._team_payload(team)
        result["message"] = "Team created."
        self.emit_web_event("teams-changed", result)
        return result

    @task_tab_action("delete-team")
    def delete_team(self, payload):
        team = self._team(payload.get("team"))
        delete_custom_team(team)
        reloaded = self._reload_live_team_code(team)
        result = {"team": list(team), "message": "Team deleted.", "reloaded": reloaded}
        self.emit_web_event("teams-changed", result)
        return result

    @task_tab_action("save-member")
    def save_member(self, payload):
        team = self._team(payload.get("team"))
        class_name = str(payload.get("class_name") or "")
        char_cls = self._character_class(class_name)
        code = payload.get("code")
        if not isinstance(code, str):
            raise ValueError("Character code must be text")
        save_team_char_code(team, char_cls, code)
        reloaded = self._reload_live_team_code(team)
        result = self._member_payload(team, class_name)
        result.update({
            "message": "Team character code saved and reloaded." if reloaded else "Team character code saved.",
            "reloaded": reloaded,
        })
        self.emit_web_event("team-member-changed", result)
        return result

    @task_tab_action("reset-member")
    def reset_member(self, payload):
        team = self._team(payload.get("team"))
        class_name = str(payload.get("class_name") or "")
        char_cls = self._character_class(class_name)
        save_team_char_code(team, char_cls, read_builtin_char_code(char_cls))
        reloaded = self._reload_live_team_code(team)
        result = self._member_payload(team, class_name)
        result.update({"message": "Character code reset to built in.", "reloaded": reloaded})
        self.emit_web_event("team-member-changed", result)
        return result

    @task_tab_action("export-team")
    def export_team(self, payload):
        team = self._team(payload.get("team"))
        metadata = {
            key: payload.get(key)
            for key in ("name", "description", "author", "version")
        }
        with tempfile.TemporaryDirectory(prefix="ok-ww-team-export-") as temp_dir:
            archive = export_custom_team(team, temp_dir, **metadata)
            content = Path(archive).read_bytes()
            return {
                "filename": Path(archive).name,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "message": "Team exported.",
            }

    @task_tab_action("import-team")
    def import_team(self, payload):
        encoded = payload.get("content_base64")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("Team archive is required")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("Team archive is not valid base64") from error
        if len(content) > MAX_IMPORT_BYTES:
            raise ValueError("Team archive is too large")
        with tempfile.TemporaryDirectory(prefix="ok-ww-team-import-") as temp_dir:
            archive = Path(temp_dir) / "team.zip"
            archive.write_bytes(content)
            info = inspect_team_archive(archive)
            team = self._team(info["team"])
            import_custom_team(info)
        reloaded = self._reload_live_team_code(team)
        result = self._team_payload(team)
        result.update({
            "manifest": info["manifest"],
            "message": "Team imported and reloaded." if reloaded else "Team imported.",
            "reloaded": reloaded,
        })
        self.emit_web_event("teams-changed", result)
        return result

    def _reload_live_team_code(self, team):
        expected = normalize_team(team)
        reloaded = 0
        for task in self.get_tasks():
            chars = getattr(task, "chars", None)
            if not chars or len(chars) != 3 or any(char is None for char in chars):
                continue
            infos = [char_dict.get(getattr(char, "char_name", None)) for char in chars]
            if any(info is None for info in infos):
                continue
            if normalize_team(info["cls"] for info in infos) != expected:
                continue
            old_types = tuple(type(char) for char in chars)
            apply_team_char_classes(task, chars)
            reloaded += sum(old_type is not type(char) for old_type, char in zip(old_types, chars))
        return reloaded

    def _load_feature_index(self):
        result = {}
        coco_path = Path(__file__).resolve().parents[2] / "assets" / "coco_annotations.json"
        if not coco_path.is_file():
            return result
        try:
            data = json.loads(coco_path.read_text(encoding="utf-8"))
            images = {item["id"]: item["file_name"] for item in data.get("images", [])}
            categories = {item["id"]: item["name"] for item in data.get("categories", [])}
            for annotation in data.get("annotations", []):
                label = categories.get(annotation.get("category_id"))
                filename = images.get(annotation.get("image_id"))
                bbox = annotation.get("bbox", [])
                if label and filename and len(bbox) == 4:
                    result[label] = (coco_path.parent / filename, tuple(round(value) for value in bbox))
        except (OSError, TypeError, ValueError, KeyError) as error:
            self.logger.error(f"load character image index failed: {error}")
        return result

    def _character_image_data_url(self, char_cls):
        class_name = char_cls.__name__
        if class_name in self._image_cache:
            return self._image_cache[class_name]
        if self._feature_index is None:
            self._feature_index = self._load_feature_index()
        _characters, labels = self._characters()
        image_info = self._feature_index.get(labels.get(class_name))
        if image_info is None:
            self._image_cache[class_name] = None
            return None
        image_path, (x, y, width, height) = image_info
        try:
            with Image.open(image_path) as source:
                image = source.crop((x, y, x + width, y + height))
                image.thumbnail((48, 48), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="PNG")
            value = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except (OSError, ValueError) as error:
            self.logger.error(f"load character image failed for {class_name}: {error}")
            value = None
        self._image_cache[class_name] = value
        return value
