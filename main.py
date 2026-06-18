import json
import atexit
import asyncio
import logging
import sqlite3
from os import getenv
from pathlib import Path
from datetime import datetime, timedelta

import Levenshtein
from dotenv import load_dotenv
from aiogram import F, Bot, Dispatcher
from unidecode import unidecode
from aiogram.types import Message
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdated,
    ChatMemberUpdatedFilter,
)

LOG_PATH: str = "logfile.log"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S.%f%:z"
MESSAGE_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S%:z"

BOT: Bot

BANNED_PHRASES: list[str] = []
VALID_CHATS: list[int] = []
ADMINS: list[dict[str, int | str]] = []

dispatcher = Dispatcher()


class LoggingFormatter(logging.Formatter):
    """Override logging.Formatter to use aware datetime objects."""

    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created).astimezone()

        if datefmt:
            return dt.strftime(datefmt)

        return dt.isoformat(timespec="milliseconds")


class Text:
    recieved_private = (
        "The private message {} from user {} in chat {} is recieved."
        " Message details: "
    )
    recieved_public = (
        "The message {} from user {} in chat {} is recieved. Message details: "
    )
    recieved_join = "The user {} joined chat {}."

    unsupported_chat = (
        "Chat {} is not supported. Message {} from user {} ignored."
    )
    trusted_user = "The user {} in chat {} is trusted. Message {} ignored."
    message_valid = "The message {} from user {} in chat {} is valid."
    message_invalid = "The message {} from user {} in chat {} is invalid."

    ban_fail = "Failed to block the user {} in chat {} from message {}."
    ban_success = (
        "Successfully blocked the user {} in chat {} from message {}."
    )


def normalize_text(text: str) -> str:
    return unidecode(text).lower().replace(" ", "").replace("\n", "")


def is_in_valid_chat(message: Message) -> bool:
    return message.chat.id in VALID_CHATS


def is_trusted(message: Message) -> bool:
    if message.from_user is None:
        # The `from_user` field may be empty for messages sent to
        # channels, so assume the user to be trusted.
        # Source: https://core.telegram.org/bots/api#message
        return True

    return any(
        (
            message.from_user.is_bot,
            (message.from_user.id in [admin["id"] for admin in ADMINS]),
            (message.from_user.id == 777000),  # noqa: PLR2004, ID 777000 is Telegram itself and is trusted
        ),
    )


async def is_valid(message: Message) -> bool:
    if message.from_user is None:
        # The `from_user` field may be empty for messages sent to
        # channels, so assume the message to be valid.
        # Source: https://core.telegram.org/bots/api#message
        return True

    user_chat_full_info = await BOT.get_chat(chat_id=message.from_user.id)
    user_bio = user_chat_full_info.bio

    return all(
        (
            validate_text(user_bio),
            validate_text(message.text),
            validate_text(message.caption),
        ),
    )


def validate_text(text: str | None) -> bool:
    """Return `True` if `text` is valid, return `False` otherwise"""

    min_validatable_length = 20
    max_valid_ratio = 0.65

    if text is None:
        return True

    if len(text) < min_validatable_length:
        # Assume the text to be always valid if it is short
        return True

    text = normalize_text(text)

    ratios: list[float] = [0]

    for phrase in BANNED_PHRASES:
        if phrase in text:
            return False

        ratio = Levenshtein.ratio(phrase, text)
        ratios.append(ratio)

    return max(ratios) < max_valid_ratio


def adapt_datetime(value: datetime) -> str:
    """Adapt a `datetime.datetime` object to an ISO 8601 date."""
    return value.isoformat(sep=" ", timespec="seconds")


def convert_datetime(value: bytes) -> datetime:
    """Convert an ISO 8601 datetime to a `datetime.datetime` object."""
    return datetime.fromisoformat(value.decode())


async def process_invalid_message(
    message_id: int,
    user_id: int,
    chat_id: int,
) -> None:
    logger.info(Text.message_invalid.format(message_id, user_id, chat_id))

    result = db_cursor.execute(
        "SELECT first_seen, violations FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if result is None:
        first_seen = datetime.now().astimezone()
        member_time = timedelta(0)
        violations = 1
    else:
        first_seen, violations = result
        member_time = datetime.now().astimezone() - first_seen
        violations += 1

    db_cursor.execute(
        """INSERT INTO users (id, first_seen, violations)
        VALUES (?, ?, ?)
        ON CONFLICT (id)
        DO UPDATE SET violations = ?""",
        (user_id, first_seen, violations, violations),
    )
    db_connection.commit()

    violation_limits: tuple[tuple[timedelta, int], ...] = (
        (timedelta(days=2), 1),
        (timedelta(days=14), 4),
    )
    max_violations: int = 8

    is_over_limit: bool = False

    if violations >= max_violations:
        is_over_limit = True
    else:
        for delta, limit in violation_limits:
            if (member_time < delta) and (violations >= limit):
                is_over_limit = True
                break

    if is_over_limit:
        try:
            await BOT.delete_message(chat_id=chat_id, message_id=message_id)
            await BOT.ban_chat_member(chat_id=chat_id, user_id=user_id)

        except TelegramBadRequest:
            logger.info(Text.ban_fail.format(user_id, chat_id, message_id))

        else:
            logger.info(Text.ban_success.format(user_id, chat_id, message_id))


async def format_message_data(message: Message) -> str:
    if message.from_user is None:
        # The `from_user` field may be empty for messages sent to
        # channels, so set the child fields to placeholder values.
        # Source: https://core.telegram.org/bots/api#message

        user_id = 0
        user_name = "Unknown"

    else:
        user_id = message.from_user.id
        user_name = message.from_user.full_name

    chat_id = message.chat.id
    chat_title = message.chat.title
    user_bio = (await BOT.get_chat(chat_id=user_id)).bio
    message_id = message.message_id
    message_date = message.date.astimezone().strftime(MESSAGE_DATE_FORMAT)
    message_text = message.text

    data = {
        "date": message_date,
        "user_id": user_id,
        "user_name": user_name,
        "user_bio": user_bio,
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_title": chat_title,
        "text": message_text,
    }

    return json.dumps(data, ensure_ascii=False)




@dispatcher.message(F.chat.type == "private")
async def private_message_handler(message: Message) -> None:
    # This assertion should never fail, because if a user can send a
    # private message to a bot, they always have valid `from_user`
    # field.
    assert message.from_user is not None  # noqa: S101

    user_id = message.from_user.id
    chat_id = message.chat.id
    message_id = message.message_id

    log_text = Text.recieved_private.format(message_id, user_id, chat_id)
    log_text += await format_message_data(message)

    logger.info(log_text)


@dispatcher.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def user_join_handler(event: ChatMemberUpdated) -> None:
    user_id = event.from_user.id
    chat_id = event.chat.id

    logger.info(Text.recieved_join.format(user_id, chat_id))


@dispatcher.message()
async def message_handler(message: Message) -> None:
    if message.from_user is None:
        # The `from_user` field may be empty for messages sent to
        # channels, so set the child fields to placeholder values.
        # Source: https://core.telegram.org/bots/api#message

        user_id = 0

    else:
        user_id = message.from_user.id

    chat_id = message.chat.id
    message_id = message.message_id

    log_text = Text.recieved_public.format(message_id, user_id, chat_id)
    log_text += await format_message_data(message)

    logger.info(log_text)

    if not is_in_valid_chat(message):
        logger.info(Text.unsupported_chat.format(chat_id, message_id, user_id))
        return

    if is_trusted(message):
        logger.info(Text.trusted_user.format(user_id, chat_id, message_id))
        return

    if await is_valid(message):
        logger.info(Text.message_valid.format(message_id, user_id, chat_id))

        result = db_cursor.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if result is None:
            first_seen = datetime.now().astimezone()
            violations = 0

            db_cursor.execute(
                """INSERT INTO users (id, first_seen, violations)
                VALUES (?, ?, ?)""",
                (user_id, first_seen, violations),
            )
            db_connection.commit()

        return

    await process_invalid_message(message_id, user_id, chat_id)


@dispatcher.edited_message()
async def edited_message_handler(message: Message) -> None:
    await message_handler(message)


async def main() -> None:
    global ADMINS  # noqa: PLW0602
    global BOT  # noqa: PLW0603

    token: str | None = getenv("TOKEN")
    if token is None:
        logger.critical("No bot token found, aborting")
        return

    BOT = Bot(token=token)

    # Get admins of all supported chats
    for chat_id in VALID_CHATS:
        admins = await BOT.get_chat_administrators(chat_id=chat_id)
        ADMINS.extend(
            [
                {"name": admin.user.full_name, "id": admin.user.id}
                for admin in admins
            ],
        )

    _admins = ", ".join(
        {f"{admin['name']} ({admin['id']})" for admin in ADMINS},
    )
    logger.debug(f"Current admins: {_admins}")

    # Run events dispatching
    await dispatcher.start_polling(BOT)


@atexit.register
def cleanup() -> None:
    db_cursor.close()
    db_connection.close()


if __name__ == "__main__":
    # Setup logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    formatter = LoggingFormatter(
        "%(asctime)s - %(message)s",
        datefmt=LOG_DATE_FORMAT,
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    # Load data
    load_dotenv()

    with Path("banned_phrases.json").open() as file:
        file_text = file.read()
    BANNED_PHRASES: list[str] = [
        normalize_text(phrase) for phrase in json.loads(file_text)
    ]

    with Path("valid_chats.json").open() as file:
        file_text = file.read()
    VALID_CHATS: list[int] = json.loads(file_text)

    # Setup the database
    db_connection = sqlite3.connect(
        "db.sqlite",
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    db_cursor = db_connection.cursor()

    sqlite3.register_adapter(datetime, adapt_datetime)
    sqlite3.register_converter("datetime", convert_datetime)

    # Run the bot
    asyncio.run(main())
