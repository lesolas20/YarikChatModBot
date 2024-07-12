from os import getenv
from dotenv import load_dotenv

from datetime import datetime

from collections.abc import Callable

import json
import logging
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from unidecode import unidecode


TOKEN: str = ""

LATIN_TO_CYRILLIC: dict[int: str, ...] = {}
BANNED_PHRASES: list[str, ...] = []
VALID_CHATS: list[int, ...] = []
VALIDATORS: list[Callable[[Message], bool], ...] = []
ADMIN_IDS: dict[int: list[int, ...], ...] = {}

dispatcher = Dispatcher()


def normalize(text: str) -> str:
    text = text.translate(LATIN_TO_CYRILLIC)
    text = unidecode(text)
    text = text.lower()
    text = text.replace(" ", "")
    text = text.replace("\n", "")

    return text


def is_in_valid_chat(message: Message) -> bool:
    return (message.chat.id in VALID_CHATS)


def is_trusted(message: Message) -> bool:
    return any(
        (
            message.from_user.is_bot,
            (message.from_user.id in ADMIN_IDS[message.chat.id]),
            (message.from_user.id == 777000),
        )
    )


def is_valid(message: Message) -> bool:
    return all([validator(message) for validator in VALIDATORS])


def validate_text(message: Message) -> bool:
    if message.text is not None:
        text = normalize(message.text)

        for phrase in BANNED_PHRASES:
            if phrase in text:
                return False

    return True


def format_log(
    chat_id: int,
    chat_title: str,
    user_id: int,
    user_name: str,
    message_date: datetime,
    message_text: str,
    comment: str
) -> str:
    def form_message_block(text: str) -> str:
        text = text.replace("\n", " ")

        rows = (len(text) // 68) + bool(len(text) % 68)

        message_block = ""
        for row in range(rows):
            start = (row * 68)
            end = ((row + 1) * 68)
            message_block += f"\n| {text[start:end]:68.68} |"

        return message_block

    return "".join(
        (
            f"\r+{18 * '—'}+{29 * '—'}+{21 * '—'}+",
            f"\n| {chat_id:16} | {chat_title:27.27} | {message_date!s:.19} |",
            f"\n+{18 * '—'}+{29 * '—'}+{21 * '—'}+",
            f"\n| {user_id:16} | {user_name:49.49} |",
            f"\n+{18 * '—'}+{51 * '—'}+",
            form_message_block(message_text),
            f"\n+{70 * '—'}+",
            f"\n| {comment:68} |",
            f"\n+{70 * '—'}+"
        )
    )


def log(message: Message, comment: str) -> None:
    text = format_log(
        message.chat.id,
        str(message.chat.title),
        message.from_user.id,
        str(message.from_user.full_name),
        message.date,
        str(message.text),
        comment
    )

    logger.info(text)


@dispatcher.message()
async def message_handler(message: Message) -> None:
    log(message, "The message is recieved.")

    if not is_in_valid_chat(message):
        log(message, "The chat is not supported.")
        return

    elif is_trusted(message):
        log(message, "The user is trusted.")
        return

    elif is_valid(message):
        log(message, "The message is valid.")
    else:
        log(message, "The message is invalid.")
        is_success = all(
            (
                await message.delete(),
                await message.chat.ban(message.from_user.id)
            )
        )

        if is_success:
            log(message, "The user is successfully blocked.")
        else:
            log(message, "Failed to block the user.")


@dispatcher.edited_message()
async def edited_message_handler(message: Message) -> None:
    await message_handler(message)


async def main() -> None:
    global ADMIN_IDS

    # Create bot
    bot = Bot(token=TOKEN)

    # Get admins of all supported chats
    for chat_id in VALID_CHATS:
        admins = await bot.get_chat_administrators(chat_id=chat_id)
        ADMIN_IDS[chat_id] = [admin.user.id for admin in admins]

    # Run events dispatching
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO)

    logger = logging.getLogger(__name__)

    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler("log.txt")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    # Load data
    load_dotenv()

    TOKEN: str = getenv("TOKEN")

    with open("latin2cyrillic.json", "r") as file:
        file_text = file.read()
    LATIN_TO_CYRILLIC: dict[int: str, ...] = str.maketrans(
        json.loads(file_text)
    )

    with open("banned_phrases.json", "r") as file:
        file_text = file.read()
    BANNED_PHRASES: list[str, ...] = [
        normalize(phrase)
        for phrase in json.loads(file_text)
    ]

    with open("valid_chats.json", "r") as file:
        file_text = file.read()
    VALID_CHATS: list[int, ...] = json.loads(file_text)

    VALIDATORS: list[Callable[[Message], bool], ...] = [
        validate_text,
    ]

    # Run bot
    asyncio.run(main())
