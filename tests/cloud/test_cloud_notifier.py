from pathlib import Path
from unittest.mock import patch

import pytest

from extensions.cloud.models import (
    AuthenticationRequiredError,
    CloudPageError,
    CloudPageState,
)
from extensions.cloud.notifier import SmtpFailureNotifier, SmtpSettings
from extensions.cloud.runner import CloudRunCoordinator
from tests.cloud.test_cloud_runner import make_session


def test_smtp_settings_read_password_file(tmp_path: Path):
    password_file = tmp_path / "smtp-password"
    password_file.write_text("secret-value\n", encoding="utf-8")

    settings = SmtpSettings.from_env(
        {
            "OK_WW_SMTP_ENABLED": "true",
            "OK_WW_SMTP_HOST": "smtp.example.test",
            "OK_WW_SMTP_USERNAME": "runner",
            "OK_WW_SMTP_PASSWORD_FILE": str(password_file),
            "OK_WW_SMTP_SENDER": "runner@example.test",
            "OK_WW_SMTP_RECIPIENT": "owner@example.test",
        }
    )

    assert settings is not None
    assert settings.password == "secret-value"


def test_smtp_notifier_uses_tls_authentication_and_sends_message():
    settings = SmtpSettings(
        host="smtp.example.test",
        port=587,
        username="runner",
        password="secret-value",
        sender="runner@example.test",
        recipient="owner@example.test",
    )

    with patch("extensions.cloud.notifier.smtplib.SMTP") as smtp:
        client = smtp.return_value.__enter__.return_value
        SmtpFailureNotifier(settings)(AuthenticationRequiredError("session expired"))

    smtp.assert_called_once_with("smtp.example.test", 587, timeout=30)
    client.starttls.assert_called_once()
    assert "context" in client.starttls.call_args.kwargs
    client.login.assert_called_once_with("runner", "secret-value")
    client.send_message.assert_called_once()
    message = client.send_message.call_args.args[0]
    assert message["From"] == "runner@example.test"
    assert message["To"] == "owner@example.test"
    assert message["Subject"] == "【OK-WW】云游戏登录会话已失效"
    assert "AuthenticationRequiredError: session expired" in message.get_content()
    assert "重新执行 enroll 登录初始化" in message.get_content()
    assert "本邮件不包含登录凭据" in message.get_content()


def test_smtp_notifier_sends_scheduled_task_report():
    settings = SmtpSettings(
        host="smtp.example.test",
        port=587,
        username="runner",
        password="secret-value",
        sender="runner@example.test",
        recipient="owner@example.test",
    )

    with patch("extensions.cloud.notifier.smtplib.SMTP") as smtp:
        client = smtp.return_value.__enter__.return_value
        SmtpFailureNotifier(settings).send_task_report(
            schedule_name="每日任务",
            task_name="Daily Task",
            succeeded=True,
        )

    message = client.send_message.call_args.args[0]
    assert message["Subject"] == "【OK-WW】定时任务成功：每日任务"
    assert "执行任务：Daily Task" in message.get_content()
    assert "执行结果：成功" in message.get_content()


def test_smtp_notifier_can_send_without_tls_or_authentication():
    settings = SmtpSettings(
        host="smtp.example.test",
        port=25,
        username="",
        password="",
        sender="runner@example.test",
        recipient="owner@example.test",
        use_tls=False,
    )

    with patch("extensions.cloud.notifier.smtplib.SMTP") as smtp:
        client = smtp.return_value.__enter__.return_value
        SmtpFailureNotifier(settings)(AuthenticationRequiredError("session expired"))

    client.starttls.assert_not_called()
    client.login.assert_not_called()
    client.send_message.assert_called_once()


def test_smtp_notifier_supports_implicit_ssl():
    settings = SmtpSettings(
        host="smtp.example.test",
        port=465,
        username="runner",
        password="secret-value",
        sender="runner@example.test",
        recipient="owner@example.test",
        use_tls=False,
        use_ssl=True,
    )

    with patch("extensions.cloud.notifier.smtplib.SMTP_SSL") as smtp_ssl:
        client = smtp_ssl.return_value.__enter__.return_value
        SmtpFailureNotifier(settings)(AuthenticationRequiredError("session expired"))

    smtp_ssl.assert_called_once()
    assert smtp_ssl.call_args.args == ("smtp.example.test", 465)
    assert smtp_ssl.call_args.kwargs["timeout"] == 30
    assert "context" in smtp_ssl.call_args.kwargs
    client.starttls.assert_not_called()
    client.login.assert_called_once_with("runner", "secret-value")
    client.send_message.assert_called_once()


def test_login_failure_notifies_once_and_still_closes(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.LOGIN_REQUIRED], enrolled=True
    )
    failures = []

    with pytest.raises(AuthenticationRequiredError):
        CloudRunCoordinator(
            session, profile, lambda: None, failure_notifier=failures.append
        ).run_once()

    assert len(failures) == 1
    assert isinstance(failures[0], AuthenticationRequiredError)
    assert adapter.actions == ["close"]


@pytest.mark.parametrize(
    "states",
    [
        [CloudPageState.UNKNOWN],
        [CloudPageState.ERROR],
        [CloudPageState.CLOSED],
        [CloudPageError("observation failed")],
    ],
)
def test_pre_authentication_restore_failure_notifies(tmp_path, states):
    _, profile, adapter, session = make_session(tmp_path, states, enrolled=True)
    failures = []

    with pytest.raises(AuthenticationRequiredError):
        CloudRunCoordinator(
            session, profile, lambda: None, failure_notifier=failures.append
        ).run_once()

    assert len(failures) == 1
    assert adapter.actions == ["close"]


def test_post_authentication_page_failure_does_not_notify(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.HOME, CloudPageState.ERROR], enrolled=True
    )
    failures = []

    with pytest.raises(CloudPageError):
        CloudRunCoordinator(
            session, profile, lambda: None, failure_notifier=failures.append
        ).run_once()

    assert failures == []
    assert adapter.actions == ["request_game", "close"]


def test_smtp_failure_does_not_replace_login_failure(tmp_path):
    _, profile, _, session = make_session(
        tmp_path, [CloudPageState.LOGIN_REQUIRED], enrolled=True
    )

    def fail_notification(_error):
        raise OSError("mail unavailable")

    with pytest.raises(AuthenticationRequiredError) as captured:
        CloudRunCoordinator(
            session, profile, lambda: None, failure_notifier=fail_notification
        ).run_once()

    assert any("SMTP notification also failed" in note for note in captured.value.__notes__)
