from __future__ import annotations

import ast
import re
from pathlib import Path

from app.runner.progress import DEFAULT_THROTTLE_SECONDS
from app.telegram.handlers.help import HELP_TEXT
from app.telegram.handlers.start import WELCOME
from app.telegram.keyboards import main_menu_keyboard

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\u2139\u2190-\u21FF\u23E9-\u23FF\u25A0-\u27FF\u2B00-\u2BFF]"
)


def _user_facing_string_constants() -> list[tuple[Path, str]]:
    roots = (Path("app/telegram"), Path("app/runner"))
    files = [path for root in roots for path in root.rglob("*.py")]
    files.append(Path("app/main.py"))
    strings: list[tuple[Path, str]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        strings.extend(
            (path, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
    return strings


def test_bot_ui_uses_emoji_only_for_primary_navigation() -> None:
    keyboard = main_menu_keyboard()
    labels = [button.text for row in keyboard.keyboard for button in row]

    assert all(_EMOJI.search(label) for label in labels)


def test_primary_screens_are_compact() -> None:
    assert len(WELCOME.splitlines()) <= 3
    assert len(HELP_TEXT.splitlines()) <= 18
    assert "━" not in WELCOME + HELP_TEXT


def test_main_menu_is_compact_and_plain() -> None:
    keyboard = main_menu_keyboard()
    buttons = [button for row in keyboard.keyboard for button in row]

    assert len(keyboard.keyboard) <= 4
    assert len(buttons) <= 6
    assert all(_EMOJI.search(button.text) for button in buttons)
    assert all(button.text == button.text.strip() for button in buttons)


def test_progress_notifications_are_rate_limited() -> None:
    assert DEFAULT_THROTTLE_SECONDS >= 30.0
