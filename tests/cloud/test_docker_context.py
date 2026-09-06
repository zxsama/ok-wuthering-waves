from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _dockerignore_rules() -> set[str]:
    return {
        line.strip()
        for line in (REPOSITORY_ROOT / ".dockerignore").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_docker_context_excludes_cloud_runtime_and_browser_credentials():
    rules = _dockerignore_rules()

    assert {
        "data",
        "**/browser-profile",
        "**/Cookies",
        "**/Login Data",
        "**/Local State",
        "**/Local Storage",
        "**/IndexedDB",
        "**/Session Storage",
        "**/Sessions",
    } <= rules


def test_docker_context_excludes_environment_and_secret_files():
    rules = _dockerignore_rules()

    assert {
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "**/*.env",
        "**/*.env.*",
        "secrets",
        "**/secrets",
        "**/*.secret",
        "**/*.pem",
        "**/*.key",
        "**/*.p12",
        "**/*.pfx",
    } <= rules


def test_compose_keeps_task_configuration_in_persistent_cloud_data():
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "OK_WW_CLOUD_CONFIG_DIR: /data/cloud/configs" in compose
    assert "cloud-data:/data/cloud" in compose
    assert "configs" in _dockerignore_rules()
