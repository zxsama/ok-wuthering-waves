"""Failure notifications for unattended cloud runs."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping

from .models import CloudConfigurationError


def _read_secret(value: str | None, file_name: str | None) -> str:
    if file_name:
        path = Path(file_name)
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CloudConfigurationError(f"cannot read SMTP password file: {path}") from exc
    return (value or "").strip()


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool = True
    use_ssl: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SmtpSettings | None":
        source = os.environ if env is None else env
        if source.get("OK_WW_SMTP_ENABLED", "false").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return None
        try:
            port = int(source.get("OK_WW_SMTP_PORT", "587"))
        except ValueError as exc:
            raise CloudConfigurationError("OK_WW_SMTP_PORT must be an integer") from exc
        settings = cls(
            host=source.get("OK_WW_SMTP_HOST", "").strip(),
            port=port,
            username=source.get("OK_WW_SMTP_USERNAME", "").strip(),
            password=_read_secret(
                source.get("OK_WW_SMTP_PASSWORD"), source.get("OK_WW_SMTP_PASSWORD_FILE")
            ),
            sender=source.get("OK_WW_SMTP_SENDER", "").strip(),
            recipient=source.get("OK_WW_SMTP_RECIPIENT", "").strip(),
            use_tls=source.get("OK_WW_SMTP_USE_TLS", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            use_ssl=source.get("OK_WW_SMTP_USE_SSL", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )
        if not settings.host or not settings.sender or not settings.recipient:
            raise CloudConfigurationError(
                "SMTP host, sender and recipient are required when SMTP is enabled"
            )
        return settings


class SmtpFailureNotifier:
    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings

    def __call__(self, error: Exception) -> None:
        self._send(
            "【OK-WW】云游戏登录会话已失效",
            "OK-WW 无人值守云游戏任务无法恢复登录会话。\n"
            f"具体原因：{type(error).__name__}: {error}\n"
            "请重新执行 enroll 登录初始化，并在浏览器中完成手动登录。\n"
            "安全提示：本邮件不包含登录凭据、浏览器凭据或会话数据。",
        )

    def send_task_report(
        self, *, schedule_name: str, task_name: str, succeeded: bool, details: str = ""
    ) -> None:
        result = "成功" if succeeded else "失败或已停止"
        body = (
            f"计划任务：{schedule_name}\n"
            f"执行任务：{task_name}\n"
            f"执行结果：{result}\n"
        )
        if details:
            body += f"详情：{details}\n"
        body += "安全提示：本邮件不包含登录凭据、浏览器凭据或会话数据。"
        self._send(f"【OK-WW】定时任务{result}：{schedule_name}", body)

    def _send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message.set_content(body)
        smtp_class = smtplib.SMTP_SSL if self.settings.use_ssl else smtplib.SMTP
        smtp_kwargs = {"timeout": 30}
        if self.settings.use_ssl:
            smtp_kwargs["context"] = ssl.create_default_context()
        with smtp_class(self.settings.host, self.settings.port, **smtp_kwargs) as client:
            if self.settings.use_tls and not self.settings.use_ssl:
                client.starttls(context=ssl.create_default_context())
            if self.settings.username:
                client.login(self.settings.username, self.settings.password)
            client.send_message(message)
