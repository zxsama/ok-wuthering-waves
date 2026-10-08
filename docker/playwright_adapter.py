"""Docker-specific cloud adapter factory.

The desktop default removes Playwright's ``--no-sandbox`` argument to avoid a
visible Chrome warning. Docker's restricted namespace setup requires that
argument unless the container is granted broader privileges, which we avoid.
"""

import json
import os
from pathlib import Path
from typing import Any

from extensions.cloud.models import CloudPageError
from extensions.cloud.playwright_adapter import PlaywrightCloudPageAdapter


class DockerPlaywrightCloudPageAdapter(PlaywrightCloudPageAdapter):
    """Handle container-specific Chrome startup constraints."""

    _SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")
    _DEFAULT_PROFILE = "Default"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def _profile_names(cls, profile_dir: Path) -> tuple[str, ...]:
        names = [cls._DEFAULT_PROFILE]
        local_state = cls._read_json(profile_dir / "Local State")
        if local_state is not None:
            profile_state = local_state.get("profile")
            last_used = (
                profile_state.get("last_used")
                if isinstance(profile_state, dict)
                else None
            )
            if (
                isinstance(last_used, str)
                and last_used
                and Path(last_used).name == last_used
                and "/" not in last_used
                and "\\" not in last_used
                and last_used not in {".", ".."}
                and last_used not in names
            ):
                names.append(last_used)
        return tuple(names)

    @classmethod
    def _prepare_profile_preferences(cls, profile_dir: Path) -> None:
        """Best-effort setup for a clean Chinese app-mode browser profile.

        Only normal-exit, language and translation preferences are changed.
        Cookies, local storage and every unrelated preference remain untouched.
        A malformed or unwritable existing profile is left as-is so Chrome can
        provide the authoritative startup error.
        """

        profile_names = cls._profile_names(profile_dir)
        for profile_name in profile_names:
            preferences = profile_dir / profile_name / "Preferences"
            payload = cls._read_json(preferences)
            if payload is None:
                if preferences.exists() or profile_name != cls._DEFAULT_PROFILE:
                    continue
                try:
                    preferences.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                payload = {}
            profile_state = payload.get("profile")
            if profile_state is None:
                profile_state = {}
                payload["profile"] = profile_state
            if not isinstance(profile_state, dict):
                continue
            intl = payload.get("intl")
            if intl is None:
                intl = {}
                payload["intl"] = intl
            if not isinstance(intl, dict):
                continue
            translate = payload.get("translate")
            if translate is None:
                translate = {}
                payload["translate"] = translate
            if not isinstance(translate, dict):
                continue
            profile_state["exit_type"] = "Normal"
            profile_state["exited_cleanly"] = True
            intl["accept_languages"] = "zh-CN,zh,en-US,en"
            intl["selected_languages"] = "zh-CN,zh,en-US,en"
            translate["enabled"] = False
            payload["translate_blocked_languages"] = ["zh-CN", "zh", "en"]

            temporary = preferences.with_name("Preferences.ok-ww.tmp")
            try:
                mode = preferences.stat().st_mode if preferences.exists() else 0o600
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.chmod(temporary, mode)
                temporary.replace(preferences)
            except (OSError, TypeError, ValueError):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _mark_profile_exited_cleanly(cls, profile_dir: Path) -> None:
        """Compatibility name retained for the Docker smoke probe."""

        cls._prepare_profile_preferences(profile_dir)

    def open(self, *, url: str, profile_dir: Path, visible: bool) -> None:
        # A container restart changes its hostname. Chrome then treats the
        # persistent profile's old SingletonLock as belonging to another host
        # and exits before Playwright can report a useful exception. The
        # coordinator already owns execution.lock when this method runs, so no
        # second cloud process can be using this profile while we clean it.
        for name in self._SINGLETON_FILES:
            path = profile_dir / name
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Let Chrome surface the precise profile error if an unusual
                # filesystem refuses removal.
                pass
        self._prepare_profile_preferences(profile_dir)

        # App + kiosk mode removes the browser toolbar entirely while leaving
        # the page under Playwright's persistent-context control. The URL is
        # supplied as an argv item, not through a shell, so no quoting step can
        # reinterpret it. Base open still navigates the selected page to the
        # same URL after launch and retains the normalized 1280x720 viewport.
        original_launch_args = self.launch_args
        app_args = tuple(
            argument
            for argument in original_launch_args
            if not argument.startswith("--app=")
        )
        self.launch_args = (*app_args, f"--app={url}")
        try:
            super().open(url=url, profile_dir=profile_dir, visible=visible)
            if visible:
                try:
                    self.call_page(self._ensure_fullscreen)
                except Exception as exc:
                    self.close()
                    raise CloudPageError("cannot enter cloud browser fullscreen") from exc
        finally:
            self.launch_args = original_launch_args

    @staticmethod
    def _ensure_fullscreen(page: Any) -> None:
        # Playwright resizes headed persistent windows when applying a fixed
        # viewport. That can undo Chrome's startup fullscreen flags and leave
        # Openbox decorations clipping the page. Apply fullscreen afterward
        # on the browser owner thread, keeping capture and input coordinates.
        session = page.context.new_cdp_session(page)
        try:
            window_id = session.send("Browser.getWindowForTarget")["windowId"]
            session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {"windowState": "fullscreen"},
            })
            page.wait_for_function(
                "() => window.screenX === 0 && window.screenY === 0 && "
                "window.outerWidth >= window.innerWidth && "
                "window.outerHeight >= window.innerHeight",
                timeout=5000,
            )
            bounds = session.send("Browser.getWindowBounds", {"windowId": window_id})["bounds"]
            if bounds.get("windowState") != "fullscreen":
                raise CloudPageError("cloud browser window did not enter fullscreen")
        finally:
            session.detach()


def create_playwright_adapter() -> PlaywrightCloudPageAdapter:
    return DockerPlaywrightCloudPageAdapter(
        ignore_default_args=(),
        launch_args=(
            "--deny-permission-prompts",
            "--lang=zh-CN",
            "--kiosk",
            "--start-fullscreen",
            "--window-position=0,0",
            "--window-size=1280,720",
            "--test-type",
            "--disable-infobars",
            "--disable-translate",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-features=Translate,TranslateUI",
        ),
    )
