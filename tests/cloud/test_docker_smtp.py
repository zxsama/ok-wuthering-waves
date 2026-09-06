from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (REPOSITORY_ROOT / name).read_text(encoding="utf-8")


def test_default_compose_does_not_require_or_mount_smtp_secret():
    compose = _read("compose.yaml")

    assert "OK_WW_SMTP_ENABLED: ${OK_WW_SMTP_ENABLED:-false}" in compose
    assert "OK_WW_SMTP_PASSWORD" not in compose
    assert "smtp_password" not in compose


def test_smtp_override_uses_required_file_backed_secret():
    override = _read("compose.smtp.yaml")

    assert 'OK_WW_SMTP_ENABLED: "true"' in override
    assert "OK_WW_SMTP_PASSWORD_FILE: /run/secrets/smtp_password" in override
    assert "OK_WW_SMTP_PASSWORD:" not in override
    assert "${OK_WW_SMTP_PASSWORD_SOURCE:?" in override
    assert "source: smtp_password" in override
    assert "target: smtp_password" in override


def test_smtp_example_contains_only_password_file_source():
    example = _read(".env.cloud.example")

    assert "OK_WW_SMTP_PASSWORD_SOURCE=./secrets/smtp_password" in example
    assert "OK_WW_SMTP_PASSWORD=" not in example
    assert "OK_WW_SMTP_PASSWORD_FILE=" not in example


def test_smtp_secret_source_is_ignored_by_git_and_docker():
    gitignore = _read(".gitignore").splitlines()
    dockerignore = _read(".dockerignore").splitlines()

    assert "secrets/" in gitignore
    assert "secrets" in dockerignore
    assert "**/secrets" in dockerignore
