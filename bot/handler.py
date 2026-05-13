"""Telegram message handlers using ConversationHandler state machine.

States:
  IDLE  (0) — no file cached
  READY (1) — file scanned, waiting for user requirement

Entry: /start, txt document
READY: text message → process
Fallbacks (any state): /help /cancel /done /mail /setmail /mymail
"""

import asyncio
import logging
import re
from collections import defaultdict
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .config import get_config
from .scanner import scan, fingerprint_to_prompt
from .ai import analyze
from .pipeline import run
from .reporter import Reporter
from .mailer import send as send_mail, MailError

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────
IDLE, READY = range(2)

# ── Keywords ───────────────────────────────────────────
CANCEL_KW = {"取消", "算了", "不要了", "发错了", "错误", "先不", "不转了", "不转换"}
FINISH_KW = {"完成", "结束", "好了", "够了"}
MAIL_KW = {"发送到邮箱", "发到邮箱", "发邮件", "发到kindle", "推送到kindle"}

# ── Messages ───────────────────────────────────────────
HELP_TEXT = """📚 **小说转换机器人** — 使用帮助

**三步使用：**
1️⃣ 发送 .txt 小说文件
2️⃣ 查看自动扫描结果
3️⃣ 回复需求文字（如「转epub」）

**可用命令：**
/start — 开始使用
/help — 显示此帮助
/cancel — 取消当前操作
/done — 完成并清除文件
/setmail — 设置接收邮箱（/setmail kindle@mail.com）
/mail — 发送最近转换的小说到邮箱
/mymail — 查看当前邮箱

**处理示例：**
• 「转epub」
• 「转mobi，作者一叶飘零」
• 「转epub并发送到邮箱」
• 「去除广告，转epub」

**支持的格式：** epub / mobi / azw3 / all

**快捷查询：**
• 「多少章」
• 「有重复吗」
• 「扫描详情」
• 「撤销 128」（恢复被删广告行）

遇到问题？发送 /help 重新查看此说明。"""

LOCAL_QUERY_KW = [
    (("多少章", "几章", "章节数", "章节数量"), "chapter_count"),
    (("重复", "去重", "重复章"), "duplicates"),
    (("异常", "问题", "包裹", "符号", "格式"), "anomalies"),
    (("扫描", "分析", "详情", "结构", "指纹", "预览"), "full"),
    (("你好", "hi", "hello"), "greeting"),
]


# ── Helpers ────────────────────────────────────────────

def _match_local_query(text: str) -> str | None:
    for keywords, category in LOCAL_QUERY_KW:
        if any(kw in text for kw in keywords):
            return category
    return None


def _local_answer(category: str, fingerprint: dict, filename: str) -> str:
    f = fingerprint.get("chapters", {})
    if category == "chapter_count":
        return f"📖 《{filename}》共 **{f.get('total_detected', '?')}** 章"
    if category == "duplicates":
        dups = f.get("duplicates", {})
        if dups:
            items = []
            for k, v in list(dups.items())[:10]:
                m = re.match(r"V(\d+)C(\d+)", k)
                if m:
                    items.append(f"V{m.group(1)}第{m.group(2)}章×{v}")
                else:
                    items.append(f"第{k}章×{v}")
            more = "" if len(dups) <= 10 else f"\n... 及其他 {len(dups) - 10} 个"
            return f"🔁 重复章节 ({len(dups)}个):\n{', '.join(items)}{more}"
        return "✅ 未检测到重复章节"
    if category == "anomalies":
        anoms, wrappers = f.get("anomalies", []), f.get("wrappers", [])
        parts = []
        if anoms:
            parts.append("异常: " + "; ".join(anoms[:5]))
        if wrappers:
            parts.append(f"章节包裹字符: {', '.join(wrappers[:5])}")
        return "\n".join(parts) if parts else "✅ 未检测到异常"
    if category == "full":
        return fingerprint_to_prompt(fingerprint)
    if category == "greeting":
        return f"👋 你好！我已经扫描好了 **{filename}**。\n\n• 直接告诉我你的需求（如「转epub」）\n• 问「扫描详情」查看结构分析\n• 回复「取消」放弃本次转换"
    return ""


def _build_preview(fingerprint: dict, filename: str) -> str:
    f = fingerprint.get("chapters", {})
    lines = [f"📄 **{filename}** | {f.get('total_detected', '?')} 章"]
    flags = []
    if f.get("duplicates"):
        flags.append(f"{len(f['duplicates'])} 处重复章节")
    if f.get("wrappers"):
        flags.append(f"包裹字符: {', '.join(f['wrappers'][:2])}")
    ad_info = fingerprint.get("ads", {}).get("details", {})
    if ad_info:
        flags.append(f"📢 {', '.join(v['label'] for v in list(ad_info.values())[:3])}")
    lines.append("⚠️ " + " | ".join(flags) if flags else "✅ 结构正常")
    lines.append("")
    lines.append("请描述你的处理需求：")
    lines.append("• 直接回复「转epub」使用默认设置")
    lines.append("• 「转mobi，作者xxx」指定格式和作者")
    lines.append("• 「扫描详情」查看完整结构分析")
    lines.append("• 「取消」放弃本次转换")
    return "\n".join(lines)


def _clear_cache(context):
    u = context.user_data
    old = u.pop("pending_file", None)
    u.pop("pending_fingerprint", None)
    u.pop("pending_filename", None)
    u.pop("undo_store", None)
    if old:
        try:
            Path(old).unlink(missing_ok=True)
        except OSError:
            pass


def _data_dir():
    config = get_config()
    return Path(config.get("storage", {}).get("data_dir", "./data"))


def _output_dir():
    return _data_dir() / "output"


# ── Entry handlers ─────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📚 **小说转换机器人**\n\n"
        "三步搞定：\n"
        "1️⃣ 发送 .txt 小说文件\n"
        "2️⃣ 查看自动扫描的结果预览\n"
        "3️⃣ 描述你的需求（例如「转epub」）\n\n"
        "支持格式：epub / mobi / azw3 / all\n"
        "发送邮件：/setmail 设置邮箱后使用 /mail\n"
        "发送 /help 查看完整帮助。"
    )
    return IDLE


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("⚠️ 只支持 .txt 文件")
        return IDLE
    if doc.file_size and doc.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("⚠️ 文件太大，限制 50MB")
        return IDLE

    chat_id = update.effective_chat.id
    bot = update.get_bot()
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    file_obj = await doc.get_file()
    file_path = data_dir / doc.file_name
    await file_obj.download_to_drive(str(file_path))

    status = await bot.send_message(chat_id=chat_id, text=f"🔍 正在扫描 **{doc.file_name}**...")

    try:
        fingerprint = await asyncio.to_thread(scan, str(file_path))
    except Exception as e:
        await status.edit_text(f"❌ 扫描失败: {e}")
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        return IDLE

    context.user_data["pending_file"] = str(file_path)
    context.user_data["pending_fingerprint"] = fingerprint
    context.user_data["pending_filename"] = doc.file_name

    await status.edit_text(_build_preview(fingerprint, doc.file_name))
    return READY


# ── READY state: process requirement ───────────────────

async def handle_requirement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    chat_id = update.effective_chat.id
    bot = update.get_bot()

    file_path_raw = context.user_data.get("pending_file")
    fingerprint = context.user_data.get("pending_fingerprint")
    filename = context.user_data.get("pending_filename", "小说.txt")

    if not file_path_raw or not fingerprint:
        await update.message.reply_text("⚠️ 数据丢失，请重新发送文件。")
        _clear_cache(context)
        return IDLE

    file_path = Path(file_path_raw)
    if not file_path.exists():
        await update.message.reply_text("⚠️ 文件已过期，请重新发送。")
        _clear_cache(context)
        return IDLE

    # ── Cancel / Finish ──
    if any(kw in text for kw in CANCEL_KW) or text.startswith("/cancel"):
        _clear_cache(context)
        await update.message.reply_text("✅ 已取消。发送新的 .txt 文件重新开始。")
        return IDLE
    if any(kw in text for kw in FINISH_KW) or text.startswith("/done"):
        _clear_cache(context)
        await update.message.reply_text("✅ 已完成。随时可以发送新的 .txt 文件。")
        return IDLE

    # ── Undo ──
    if text.startswith("撤销"):
        msg = await _do_undo(update, context)
        if msg:
            await update.message.reply_text(msg)
        return READY

    # ── Local query ──
    cat = _match_local_query(text)
    if cat:
        answer = _local_answer(cat, fingerprint, filename)
        await update.message.reply_text(answer)
        return READY

    # ── Process ──
    reporter = Reporter(bot, chat_id)
    user_request = text or "转epub"

    try:
        await reporter.start(filename)

        ch_info = fingerprint.get("chapters", {})
        await reporter.update("scan", f"已缓存扫描: {ch_info.get('total_detected', '?')} 章")
        if ch_info.get("anomalies"):
            await reporter.update("scan", f"发现异常: {'; '.join(ch_info['anomalies'][:2])}")

        prompt_text = fingerprint_to_prompt(fingerprint)

        await reporter.update("ai", "正在分析需求...")
        recipe = await asyncio.to_thread(analyze, prompt_text, user_request)
        await reporter.update("ai", f"分析完成: {recipe.get('reasoning', '')}")

        # Sanitize
        for k, v in [("format", "epub"), ("lang", "zh"), ("strip_wrappers", False),
                      ("strip_ad_suffixes", False), ("deduplicate_chapters", False)]:
            recipe.setdefault(k, v)
        recipe.setdefault("extra_replacements", [])
        recipe.setdefault("ad_actions", {})
        for key in ("author", "bookname", "chapter_pattern"):
            recipe.setdefault(key, None)

        logger.info("Processing: request=%s recipe=%s", user_request, recipe)

        await reporter.update("clean", "正在清理文本...")
        output_dir = _output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(run, str(file_path), recipe, str(output_dir))

        clean_stats = result.get("clean_stats", {})
        for op in clean_stats.get("operations", []):
            await reporter.update("clean", op)

        formats = recipe.get("format") or "epub"
        if isinstance(formats, str):
            formats = [formats]
        await reporter.update("convert", f"转换为 {', '.join(formats)} 中...")

        outputs = result.get("outputs", [])
        success = [o for o in outputs if "error" not in o]
        failed = [o for o in outputs if "error" in o]
        await reporter.done(f"完成！生成 {len(success)} 个文件")

        for out in success:
            out_path = Path(out["path"])
            if out_path.exists():
                with open(str(out_path), "rb") as f:
                    await bot.send_document(chat_id=chat_id, document=f, filename=out_path.name)

        if failed:
            await bot.send_message(chat_id=chat_id,
                text="⚠️ 部分格式转换失败:\n" + "\n".join(f"{f['format']}: {f['error']}" for f in failed))

        # Ad report + undo
        ad_report = clean_stats.get("ad_report")
        if ad_report and (ad_report.get("deleted") or ad_report.get("kept")):
            report_text = _format_ad_report(ad_report)
            await bot.send_message(chat_id=chat_id, text=report_text)
            if ad_report.get("deleted_original"):
                context.user_data["undo_store"] = ad_report["deleted_original"]

        # Post-conversion prompt
        umail = context.user_data.get("email", "未设置")
        await bot.send_message(chat_id=chat_id,
            text=f"💾 文件已保留，可继续操作：\n"
                 f"• 「再转个mobi」换格式再转\n"
                 f"• 「作者xxx 重来」指定参数重新处理\n"
                 f"• 「扫描详情」查看结构\n"
                 f"• 「发送到邮箱」→ {umail}\n"
                 f"• 「完成」结束并清除文件")

        # Auto-send mail if user requested it in their requirement text
        wants_mail = any(kw in user_request for kw in MAIL_KW)
        if wants_mail:
            user_email = context.user_data.get("email")
            if user_email and success:
                await _auto_mail(update, context, success[0]["path"], user_email)

    except Exception as e:
        logger.exception("Error processing: %s", e)
        await Reporter.send_error(bot, chat_id, str(e))

    return READY


# ── Mail helpers ───────────────────────────────────────

async def _auto_mail(update: Update, context, filepath: str, email: str):
    """Send file to email silently (triggered by user request keywords)."""
    try:
        await asyncio.to_thread(_send_mail_sync, email, filepath)
        await update.message.reply_text(f"📧 已发送到 {email}")
    except MailError as e:
        await update.message.reply_text(f"❌ 自动发送邮件失败\n{str(e)[:300]}")


def _send_mail_sync(email: str, filepath: str) -> None:
    config = get_config()
    send_mail(config, email, filepath)


# ── Fallback commands (work from any state) ────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(HELP_TEXT)
    return IDLE if not context.user_data.get("pending_file") else READY


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_cache(context)
    await update.message.reply_text("✅ 已取消。发送新的 .txt 文件重新开始。")
    return IDLE


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_cache(context)
    await update.message.reply_text("✅ 已完成。随时可以发送新的 .txt 文件。")
    return IDLE


async def setmail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    m = re.match(r"/setmail\s+(\S+@\S+\.\S+)", text)
    if not m:
        await update.message.reply_text("用法：/setmail your@mail.com")
        return IDLE if not context.user_data.get("pending_file") else READY
    email = m.group(1).strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        await update.message.reply_text("⚠️ 邮箱格式无效。")
        return IDLE if not context.user_data.get("pending_file") else READY
    context.user_data["email"] = email
    await update.message.reply_text(f"✅ 邮箱已保存：{email}")
    return IDLE if not context.user_data.get("pending_file") else READY


async def mymail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = context.user_data.get("email")
    if email:
        await update.message.reply_text(f"📧 当前邮箱：{email}")
    else:
        await update.message.reply_text("📧 请先设置邮箱：/setmail your@mail.com")
    return IDLE if not context.user_data.get("pending_file") else READY


async def mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = context.user_data.get("email")
    if not email:
        await update.message.reply_text("📧 请先设置邮箱：/setmail your@mail.com")
        return IDLE if not context.user_data.get("pending_file") else READY

    out_dir = _output_dir()
    if not out_dir.exists():
        await update.message.reply_text("📧 没有可发送的文件。请先转换一本小说。")
        return READY

    files = sorted(out_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if f.suffix.lower() in (".epub", ".mobi", ".azw3")]
    if not files:
        await update.message.reply_text("📧 没有可发送的文件。请先转换一本小说。")
        return IDLE if not context.user_data.get("pending_file") else READY

    target = files[0]
    status = await update.message.reply_text(f"📧 正在发送 **{target.name}** 到 {email} ...")
    try:
        await asyncio.to_thread(_send_mail_sync, email, str(target))
        await status.edit_text(f"✅ 已发送 **{target.name}** 到 {email}")
    except MailError as e:
        await status.edit_text(f"❌ 发送失败\n{str(e)[:500]}")
    return IDLE if not context.user_data.get("pending_file") else READY


# ── Undo ───────────────────────────────────────────────

async def _do_undo(update: Update, context) -> str | None:
    text = update.message.text.strip()
    undo_store = context.user_data.get("undo_store")
    if not undo_store:
        return "无撤销数据，请先执行一次转换。"
    if text in ("全部撤销", "撤销全部"):
        count = len(undo_store)
        context.user_data.pop("undo_store", None)
        return f"✅ 已撤销所有 {count} 条广告删除（需重新转换才能生效）"
    m = re.match(r"撤销\s*(\d+)", text)
    if m:
        orig = undo_store.pop(m.group(1), None)
        if orig:
            return f"✅ 已撤销行 {m.group(1)}：\n{orig[:100]}\n\n⚠️ 需重新转换才能生效。"
        return f"❌ 未找到行 {m.group(1)} 的撤销记录"
    return "❌ 无法识别的撤销指令。用法：\n• 「撤销 128」恢复行 128\n• 「全部撤销」恢复所有被删广告行"


# ── Ad report formatter ────────────────────────────────

def _format_ad_report(ad_report: dict) -> str:
    lines = ["🧹 **广告清洗报告:**"]
    by_type = defaultdict(list)
    for d in ad_report.get("deleted", [])[:30]:
        by_type[d["label"]].append(d)
    for label, items in sorted(by_type.items()):
        nums = ", ".join(str(i["line"]) for i in items[:8])
        suffix = "..." if len(items) > 8 else ""
        lines.append(f"  ✅ 删除 {label} {len(items)} 处 (行 {nums}{suffix})")
    kept = defaultdict(list)
    for k in ad_report.get("kept", [])[:20]:
        kept[k["label"]].append(k)
    for label, items in sorted(kept.items()):
        lines.append(f"  ⏸️ 保留 {label} {len(items)} 处")
    return _safe_truncate("\n".join(lines))


def _safe_truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "\n\n...(内容过长已截断)"
