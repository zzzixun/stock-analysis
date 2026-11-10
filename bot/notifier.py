"""Telegram Bot / LINE 每日盤前、盤後自動排版與推播。（待實作，見 specs/spec_notifier_bot.md）"""


def send_telegram(text: str, token: str, chat_id: str) -> None:
    raise NotImplementedError


def send_line(text: str, token: str) -> None:
    raise NotImplementedError
