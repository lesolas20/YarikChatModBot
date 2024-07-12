from os import getenv
from dotenv import load_dotenv

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from collections.abc import Callable

import json
import logging
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from unidecode import unidecode


LOG_PATH: str = "log.jsonl"
DATE_FORMAT: str = "%d.%m.%Y %H:%M:%S"

TOKEN: str = ""

LATIN_TO_CYRILLIC: dict[int: str, ...] = {}
BANNED_PHRASES: list[str, ...] = []
VALID_CHATS: list[int, ...] = []
VALIDATORS: list[Callable[[Message], bool], ...] = []
ADMINS: list[dict[str: str, str: int], ...] = []

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
            (message.from_user.id in [admin["id"] for admin in ADMINS]),
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
    chat_id: int | None,
    chat_title: str | None,
    user_id: int | None,
    user_name: str | None,
    message_date: str | None,
    message_text: str | None,
    comment: str | None
) -> str:
    return json.dumps(
        {
            "date": message_date,
            "comment": comment,
            "user_id": user_id,
            "user_name": user_name,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "text": message_text
        },
        ensure_ascii=False
    )


def log(message: Message | None, comment: str) -> None:
    if message is None:
        text = format_log(
            None,
            None,
            None,
            None,
            None,
            None,
            comment
        )

    else:
        text = format_log(
            message.chat.id,
            str(message.chat.title),
            message.from_user.id,
            str(message.from_user.full_name),
            message.date.astimezone(
                ZoneInfo("Europe/Kyiv")
            ).strftime(DATE_FORMAT),
            str(message.text),
            comment
        )

    logger.info(text)


def get_log_entries(
    filename: str,
    start: datetime,
    end: datetime
) -> list[str, ...]:
    def format_entry(entry: dict) -> str:
        mtml = message_text_max_length = 2048
        placeholder = "..."

        message_text = entry["text"]

        if (message_text is not None):
            if (len(message_text) > message_text_max_length):
                message_text_length = mtml - len(placeholder + 1)
                message_text = message_text[message_text_length]

        return "\n".join(
            (
                f"Date: {entry['date']}",
                f"Comment: {entry['comment']}",
                f"User ID: {entry['user_id']}",
                f"User name: {entry['user_name']}",
                f"Chat ID: {entry['chat_id']}",
                f"Chat title: {entry['chat_title']}",
                f"Message text: {message_text}"
            )
        )

    def stop_search(date: datetime) -> bool:
        min_delta_to_terminate = timedelta(hours=1)

        if (date is None):
            return False

        return any(
            (
                ((start - date) > min_delta_to_terminate),
                ((date - end) > min_delta_to_terminate)
            )
        )

    entries = []

    with open(filename, "r") as file:
        file_lines = file.readlines()

    for line in file_lines[::-1]:
        entry = json.loads(line)

        if entry["date"] is None:
            date = None
        else:
            date = datetime.strptime(entry["date"], DATE_FORMAT)

        if stop_search(date):
            break
        elif (date is None) or ((date >= start) and (date <= end)):
            entries.append(format_entry(entry))

    return entries[::-1]


@dispatcher.message(F.chat.type == "private")
async def private_message_handler(message: Message) -> None:
    log(message, "The private message is recieved.")

    admin_ids = [admin["id"] for admin in ADMINS]
    if (message.from_user.id in admin_ids) and message.text:

        if message.text.startswith("/log"):
            units_table = {"m": "minutes", "h": "hours" , "d": "days"}

            unit = message.text[-1]

            if unit in units_table.keys():
                value = message.text[4:-1][:9]

                try:
                    value = int(value)
                except ValueError:
                    value = 0

                if value > 0:
                    unit = units_table[unit]

                    now = datetime.now()
                    delta = timedelta(**{unit: value})
                    start = now - delta

                    entries = get_log_entries(
                        filename=LOG_PATH,
                        start=start,
                        end=now
                    )

                    for entry in entries:
                        await message.answer(entry)

                    return

    await message.answer("Invalid command")


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
    global ADMINS

    # Create bot
    bot = Bot(token=TOKEN)

    # Get admins of all supported chats
    for chat_id in VALID_CHATS:
        admins = await bot.get_chat_administrators(chat_id=chat_id)
        ADMINS.extend(
            [
                {"name": admin.user.full_name, "id": admin.user.id}
                for admin in admins
            ]
        )

    _admins = ", ".join(
        [f"{admin['name']} ({admin['id']})" for admin in ADMINS]
    )
    log(None, f"Current admins: {_admins}")

    # Run events dispatching
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO)

    logger = logging.getLogger(__name__)

    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(LOG_PATH)
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
