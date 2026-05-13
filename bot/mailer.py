"""Email sending — multi-provider dispatch.

Supports:
  smtp    — SMTP (QQ, 163, Gmail, legacy Outlook Basic Auth, etc.)
  outlook — Microsoft Graph API (OAuth 2.0, required after Oct 2024)

Configure via config.yaml:
  mail:
    provider: smtp | outlook
    smtp: { smtp_server, smtp_port, sender, password }
    outlook: { client_id, tenant_id, client_secret, refresh_token, sender }

Backward compatible: if no mail.provider set but top-level smtp key
exists, it acts as provider=smtp automatically.
"""

import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class MailError(Exception):
    """Raised when email sending fails for any reason."""


def send(config: dict, to_email: str, filepath: str) -> None:
    """Send a file as email attachment using the configured provider.

    Args:
        config: full app config dict (e.g. yaml.safe_load result)
        to_email: recipient email address
        filepath: path to the file to attach

    Raises:
        MailError
    """
    mail_cfg = config.get("mail", {})
    provider = mail_cfg.get("provider")

    # Backward compat: old-style top-level smtp block
    if not provider and config.get("smtp"):
        smtp_cfg = config["smtp"]
        _send_smtp(smtp_cfg, to_email, filepath)
        return

    if provider == "outlook":
        from .mail_outlook import send as _outlook_send, OutlookMailError
        try:
            _outlook_send(mail_cfg["outlook"], to_email, filepath)
        except OutlookMailError as e:
            raise MailError(str(e)) from e
        return

    # Default: smtp
    smtp_cfg = mail_cfg.get("smtp") or config.get("smtp", {})
    _send_smtp(smtp_cfg, to_email, filepath)


def _send_smtp(smtp_config: dict, to_email: str, filepath: str) -> None:
    """Internal SMTP send logic."""
    required = ("smtp_server", "smtp_port", "sender", "password")
    missing = [k for k in required if k not in smtp_config]
    if missing:
        raise MailError("SMTP 未配置。请在 config.yaml 的 mail.smtp 中设置邮箱和授权码（缺少: {}）".format(
            ", ".join(missing)))

    file_path = Path(filepath)
    if not file_path.exists():
        raise MailError(f"文件不存在: {filepath}")

    filename = file_path.name
    msg = MIMEMultipart()
    msg["From"] = smtp_config["sender"]
    msg["To"] = to_email
    msg["Subject"] = f"[NovelBot] {file_path.stem}"
    msg.attach(MIMEText(f"这是你通过 NovelBot 转换的电子书：{filename}\n\nEnjoy reading!\n\n— NovelBot",
                        "plain", "utf-8"))

    try:
        with open(filepath, "rb") as fh:
            part = MIMEApplication(fh.read(), _subtype="octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    except OSError as e:
        raise MailError(f"读取文件失败: {filepath}") from e

    server = smtp_config["smtp_server"]
    port = int(smtp_config["smtp_port"])

    try:
        with smtplib.SMTP(server, port, timeout=15) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(smtp_config["sender"], smtp_config["password"])
            conn.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("SMTP 认证失败，请检查邮箱和密码") from e
    except smtplib.SMTPConnectError as e:
        raise MailError(f"无法连接 SMTP 服务器 {server}:{port}") from e
    except smtplib.SMTPException as e:
        raise MailError(f"SMTP 发送失败: {e}") from e
    except OSError as e:
        raise MailError(f"网络错误: {e}") from e

    logger.info("Sent %s to %s via %s", filename, to_email, server)
