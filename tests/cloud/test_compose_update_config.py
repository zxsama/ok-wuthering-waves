import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def compose_config(directory, files, extra_env=None, env_file=None):
    args = ["docker", "compose", "-p", "update-config-test"]
    if env_file:
        args.extend(["--env-file", str(env_file)])
    for file in files:
        args.extend(["-f", str(file)])
    args.extend(["config", "--format", "json"])
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("CLOUD_", "OK_WW_", "COMPOSE_"))
    }
    return json.loads(subprocess.check_output(
        args, cwd=directory, env={**env, **(extra_env or {})}, text=True,
    ))


@unittest.skipUnless(shutil.which("docker"), "requires Docker Compose CLI")
class ComposeUpdateConfigTest(unittest.TestCase):
    def test_runner_configuration_survives_nested_compose(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            host = directory / "checkout"
            workspace = directory / "workspace"
            for location in (host, workspace):
                location.mkdir()
                for name in ("compose.yaml", "compose.smtp.yaml"):
                    shutil.copyfile(ROOT / name, location / name)

            env_file = directory / "launch.env"
            env_file.write_text(
                "CLOUD_IMAGE=local/compose-test:custom\n"
                "CLOUD_RESTART_POLICY=unless-stopped\n"
                "CLOUD_COMMAND=serve\nCLOUD_SCREEN=1600x900x24\n"
                "CLOUD_WEB_PORT=9123\nCLOUD_WEB_HOST_PORT=19123\n"
                "CLOUD_NOVNC_PORT=19124\nCLOUD_NOVNC_INTERNAL_PORT=9124\n"
                "CLOUD_DNS_PRIMARY=192.0.2.1\nCLOUD_DNS_SECONDARY=192.0.2.2\n"
                "CLOUD_SHM_SIZE=512mb\nCLOUD_LOG_MAX_SIZE=4m\nCLOUD_LOG_MAX_FILES=2\n"
                "OK_WW_SMTP_HOST=mail.example.test\n"
                "OK_WW_SMTP_PASSWORD_SOURCE=../private/mail password\n",
                encoding="utf-8",
            )
            for smtp in (False, True):
                with self.subTest(smtp=smtp):
                    files = ["compose.yaml", "compose.smtp.yaml"] if smtp else ["compose.yaml"]
                    outer = compose_config(host, files, {"CLOUD_WEB_HOST_PORT": "29123"}, env_file)
                    updater = outer["services"]["auto-updater"]
                    self.assertFalse(updater.get("secrets"))
                    inner_env = dict(updater["environment"])
                    inner_files = [Path(inner_env["CLOUD_UPDATE_COMPOSE_FILE"]).name]
                    if smtp:
                        inner_files.append(Path(inner_env["CLOUD_UPDATE_SMTP_OVERLAY"]).name)
                        # The updater inspects the existing runner mount for its
                        # daemon-side source before invoking nested Compose.
                        secret_path = outer["secrets"]["smtp_password"]["file"]
                        inner_env["OK_WW_SMTP_PASSWORD_SOURCE"] = secret_path
                    inner = compose_config(workspace, inner_files, inner_env)
                    self.assertEqual(inner.get("secrets"), outer.get("secrets"))
                    runner = inner["services"]["cloud-runner"]
                    expected = outer["services"]["cloud-runner"]
                    # The same checkout is mounted at a different client path.
                    self.assertEqual(runner["build"].pop("context"), str(workspace))
                    self.assertEqual(expected["build"].pop("context"), str(host))
                    self.assertEqual(runner, expected)
