import json
import atexit
import asyncio
import logging
import contextlib
from datetime import datetime, timedelta

import Levenshtein
from aiogram import F, Bot, Dispatcher
from aiogram.enums import ChatType
from aiogram.types import User, Message
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiogram.filters.chat_member_updated import (
    ChatMemberUpdated,
    ChatMemberUpdatedFilter,
)

from utils.misc import normalize_text
from utils.config import Config, load_config
from utils.logging import setup as setup_logging
from utils.database import Database

MESSAGE_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S%:z"

dispatcher = Dispatcher()


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


def is_in_valid_chat(message: Message, valid_chats: list[int]) -> bool:
    return message.chat.id in valid_chats


async def is_trusted(
    bot: Bot,
    user: User | None,
    valid_chats: list[int],
) -> bool:
    if user is None:
        # `user` may be `None` for messages sent to
        # channels, so assume the user to be trusted.
        # Source: https://core.telegram.org/bots/api#message
        return True

    admins: list[int] = []

    for chat_id in valid_chats:
        try:
            chat_admins = await bot.get_chat_administrators(chat_id=chat_id)
        except AiogramError:
            continue

        admins.extend(member.user.id for member in chat_admins)

    return any(
        (
            user.is_bot,
            (user.id in admins),
            (user.id == 777000),  # noqa: PLR2004, ID 777000 is Telegram itself and is trusted
        ),
    )


async def is_valid(
    bot: Bot,
    message: Message,
    banned_phrases: list[str],
) -> bool:
    if message.from_user is None:
        # The `from_user` field may be empty for messages sent to
        # channels, so assume the message to be valid.
        # Source: https://core.telegram.org/bots/api#message
        return True

    user_chat_full_info = await bot.get_chat(chat_id=message.from_user.id)
    user_bio = user_chat_full_info.bio

    return all(
        (
            validate_text(user_bio, banned_phrases),
            validate_text(message.text, banned_phrases),
            validate_text(message.caption, banned_phrases),
        ),
    )


def validate_text(text: str | None, banned_phrases: list[str]) -> bool:
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

    for phrase in banned_phrases:
        if phrase in text:
            return False

        ratio = Levenshtein.ratio(phrase, text)
        ratios.append(ratio)

    return max(ratios) < max_valid_ratio


async def process_invalid_message(
    bot: Bot,
    message_id: int,
    user_id: int,
    chat_id: int,
    database: Database,
) -> None:
    logger.info(Text.message_invalid.format(message_id, user_id, chat_id))

    first_seen = database.get_user_first_seen(user_id)

    if first_seen is None:
        first_seen = datetime.now().astimezone()
        member_time = timedelta(0)
        violations = 1

    else:
        member_time = datetime.now().astimezone() - first_seen
        violations = database.get_user_violations(user_id)

        if violations is None:
            violations = 0

        violations += 1

    database.insert_or_update_user(user_id, first_seen, violations)

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
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)

        except TelegramBadRequest:
            logger.info(Text.ban_fail.format(user_id, chat_id, message_id))

        else:
            logger.info(Text.ban_success.format(user_id, chat_id, message_id))


async def format_message_data(bot: Bot, message: Message) -> str:
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
    user_bio = (await bot.get_chat(chat_id=user_id)).bio
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
    log_text += await format_message_data(bot, message)

    logger.info(log_text)


@dispatcher.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def user_join_handler(event: ChatMemberUpdated) -> None:
    user_id = event.from_user.id
    chat_id = event.chat.id

    logger.info(Text.recieved_join.format(user_id, chat_id))


@dispatcher.message(F.chat.type.in_((ChatType.GROUP, ChatType.SUPERGROUP)))
@dispatcher.edited_message(
    F.chat.type.in_((ChatType.GROUP, ChatType.SUPERGROUP)),
)
async def message_handler(
    message: Message,
    bot: Bot,
    config: Config,
    database: Database,
) -> None:
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
    log_text += await format_message_data(bot, message)

    logger.info(log_text)

    if not is_in_valid_chat(message, config.valid_chats):
        logger.info(Text.unsupported_chat.format(chat_id, message_id, user_id))
        return

    if await is_trusted(bot, message.from_user, config.valid_chats):
        logger.info(Text.trusted_user.format(user_id, chat_id, message_id))
        return

    if await is_valid(bot, message, config.banned_phrases):
        logger.info(Text.message_valid.format(message_id, user_id, chat_id))

        if database.user_exists(user_id):
            return

        first_seen = datetime.now().astimezone()
        violations = 0

        database.insert_or_update_user(user_id, first_seen, violations)

        return

    await process_invalid_message(bot, message_id, user_id, chat_id, database)


@atexit.register
def cleanup() -> None:
    with contextlib.suppress(NameError):
        database.close()


if __name__ == "__main__":
    logger = logging.getLogger(__name__)

    setup_logging()

    config = load_config()

    database = Database("db.sqlite")

    bot = Bot(token=config.bot_token)

    # Run the bot
    asyncio.run(
        dispatcher.start_polling(
            bot,
            config=config,
            database=database,
        ),
    )
