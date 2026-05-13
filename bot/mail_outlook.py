"""Outlook mail via Microsoft Graph API (OAuth 2.0).

Requires: msal (Microsoft Authentication Library)
One-time setup: run setup_outlook.py to get refresh_token.
"""

import base64
import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class OutlookMailError(Exception):
    """Raised when Outlook Graph API send fails."""


def send(cfg: dict, to_email: str, filepath: str) -> None:
    """Send email via Microsoft Graph API using OAuth refresh_token flow.

    Args:
        cfg: dict with client_id, tenant_id, client_secret, refresh_token, sender
        to_email: recipient
        filepath: path to attachment file
    """
    required = ("client_id", "tenant_id", "client_secret", "refresh_token", "sender")
    missing = [k for k in required if k not in cfg or not cfg[k]]
    if missing:
        raise OutlookMailError(f"Outlook 配置不完整，缺少: {', '.join(missing)}")

    file_path = Path(filepath)
    if not file_path.exists():
        raise OutlookMailError(f"文件不存在: {filepath}")

    filename = file_path.name
    access_token = _acquire_token(cfg)

    with open(filepath, "rb") as fh:
        file_bytes = fh.read()
    content_b64 = base64.b64encode(file_bytes).decode("ascii")

    # Determine content type from extension
    ext = file_path.suffix.lower()
    content_type_map = {".epub": "application/epub+zip", ".mobi": "application/x-mobipocket-ebook",
                        ".azw3": "application/vnd.amazon.ebook", ".pdf": "application/pdf"}
    content_type = content_type_map.get(ext, "application/octet-stream")

    body = {
        "message": {
            "subject": f"[NovelBot] {file_path.stem}",
            "body": {
                "contentType": "Text",
                "content": f"这是你通过 NovelBot 转换的电子书：{filename}\n\nEnjoy reading!\n\n— NovelBot",
            },
            "toRecipients": [{"emailAddress": {"address": to_email}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": filename,
                "contentType": content_type,
                "contentBytes": content_b64,
            }],
        },
        "saveToSentItems": "true",
    }

    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{cfg['sender']}/sendMail",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )

    if resp.status_code == 202:
        logger.info("Sent %s to %s via Outlook Graph API", filename, to_email)
        return

    # Try to parse error detail
    detail = resp.text[:500]
    try:
        err = json.loads(detail)
        detail = err.get("error", {}).get("message", detail)
    except (json.JSONDecodeError, AttributeError):
        pass

    raise OutlookMailError(f"Outlook 发送失败 ({resp.status_code}): {detail}")


def _acquire_token(cfg: dict) -> str:
    """Acquire access_token using refresh_token (non-interactive)."""
    import msal

    app = msal.ConfidentialClientApplication(
        cfg["client_id"],
        authority=f"https://login.microsoftonline.com/{cfg['tenant_id']}",
        client_credential=cfg["client_secret"],
    )

    result = app.acquire_token_by_refresh_token(cfg["refresh_token"], scopes=["https://graph.microsoft.com/Mail.Send"])

    if "access_token" in result:
        return result["access_token"]

    error = result.get("error_description") or result.get("error") or str(result)
    raise OutlookMailError(f"Outlook 认证失败: {error}\n请重新运行 setup_outlook.py 获取 token")
