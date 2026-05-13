"""Progress reporter: sends status updates to the user via Telegram."""

from telegram import Bot
from telegram.error import TelegramError


class Reporter:
    """Manages a status message that gets updated at each phase."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = None
        self._lines = []

    async def start(self, filename: str) -> int:
        """Send initial status message, return message_id."""
        text = f"📄 **{filename}**\n\n🔍 正在扫描小说结构..."
        msg = await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
        )
        self.message_id = msg.message_id
        self._lines = [text]
        return msg.message_id

    async def update(self, phase: str, detail: str = ""):
        """Append a status line."""
        emoji_map = {
            "scan": "🔍",
            "ai": "🤖",
            "clean": "🧹",
            "convert": "⚙️",
            "done": "✅",
            "error": "❌",
        }
        emoji = emoji_map.get(phase, "•")
        line = f"{emoji} {detail}" if detail else f"{emoji} {phase}"
        self._lines.append(line)
        text = "\n".join(self._lines)
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
            )
        except TelegramError:
            pass

    async def done(self, summary: str):
        """Send final completion message."""
        self._lines.append(f"\n✅ {summary}")
        text = "\n".join(self._lines)
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
            )
        except TelegramError:
            pass

    @staticmethod
    async def send_error(bot: Bot, chat_id: int, error: str):
        """Send a standalone error message."""
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ 处理失败\n{error[:1000]}",
        )
