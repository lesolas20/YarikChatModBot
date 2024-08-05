from os import getenv
from dotenv import load_dotenv

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from functools import cache

from collections.abc import Callable

import json
import logging
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    Message, CallbackQuery,
    LinkPreviewOptions,
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
)
from aiogram.exceptions import TelegramBadRequest

from unidecode import unidecode


LOG_PATH: str = "log.jsonl"
DATE_FORMAT: str = "%d.%m.%Y %H:%M:%S"

TIMEZONE = "Europe/Kyiv"

TOKEN: str = ""

BOT: Bot = None

LATIN_TO_CYRILLIC: dict[int, str] = {}
BANNED_PHRASES: list[str, ...] = []
VALID_CHATS: list[int, ...] = []
ADMINS: list[dict[str, int], ...] = []

LOGS_MENU = (
    ("Last 15 minutes", 15, "minutes"),
    ("Last hour", 1, "hours"),
    ("Last 4 hours", 4, "hours"),
    ("Last 24 hours", 1, "days"),
    ("Last week", 7, "days"),
    ("Last month", 30, "days"),
)

dispatcher = Dispatcher()


class Text:
    ban = "Ban"
    unban = "Unban"
    select_logs = "Select the time frame to view the logs:"
    no_data = "No data available"
    invalid_command = "Invalid command"
    forbidden_command = "You are not allowed to use this command"
    f_ban_fail = "Failed to block user {} in chat {} from message {}"
    f_ban_success = "\n".join(
        (
            "Successfully blocked the user.",
            "User ID: {}",
            "Chat ID: {}",
            "Message ID: {}"
        )
    )
    f_unban_fail = "Failed to unblock user {} in chat {} from message {}"
    f_unban_success = "\n".join(
        (
            "Successfully unblocked the user.",
            "User ID: {}",
            "Chat ID: {}",
            "Message ID: {}"
        )
    )
    log_recieved_private = "The private message is recieved."
    log_displayed_logs = "Displayed the log entries."
    log_no_logs_data = "No log entries to display."
    log_recieved_public = "The message is recieved."
    log_unsupported_chat = "The chat is not supported."
    log_trusted_user = "The user is trusted."
    log_message_valid = "The message is valid."
    log_message_invalid = "The message is invalid."
    log_f_ban_fail = "Failed to block user {} in chat {} from message {}"
    log_f_ban_success = "Successfully blocked user {} in chat {} from message {}"
    log_f_unban_fail = "Failed to unblock user {} in chat {}"
    log_f_unban_success = "Successfully unblocked user {} in chat {}"


class LogsMenuCallback(CallbackData, prefix="log", sep=" "):
    time_value: int
    time_unit: str


class BanUserCallback(CallbackData, prefix="ban", sep=" "):
    pass


class UnbanUserCallback(CallbackData, prefix="unban", sep=" "):
    pass


@cache
def get_logs_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=LogsMenuCallback(
                        time_value=time_value,
                        time_unit=time_unit
                    ).pack()
                )
            ]
            for text, time_value, time_unit in LOGS_MENU
        ]
    )


@cache
def get_ban_user_keyboard() -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=Text.ban,
        callback_data=BanUserCallback().pack()
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


@cache
def get_unban_user_keyboard() -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=Text.unban,
        callback_data=UnbanUserCallback().pack()
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


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


async def is_valid(message: Message) -> bool:
    user_bio = (await BOT.get_chat(chat_id=message.from_user.id)).bio
    return validate_text(user_bio) and validate_text(message.text)


def validate_text(text: str | None) -> bool:
    if text is None:
        return True

    text = normalize(text)

    for phrase in BANNED_PHRASES:
        if phrase in text:
            return False

    return True


def format_log(
    chat_id: int | None = None,
    chat_title: str | None = None,
    user_id: int | None = None,
    user_name: str | None = None,
    user_bio: str | None = None,
    message_id: int | None = None,
    message_date: str | None = None,
    message_text: str | None = None,
    comment: str | None = None
) -> str:
    return json.dumps(
        {
            "date": message_date,
            "comment": comment,
            "user_id": user_id,
            "user_name": user_name,
            "user_bio": user_bio,
            "message_id": message_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "text": message_text
        },
        ensure_ascii=False
    )


async def log(message: Message | None, comment: str) -> None:
    if message is None:
        text = format_log(
            message_date=datetime.now().astimezone(
                ZoneInfo(TIMEZONE)
            ).strftime(DATE_FORMAT),
            comment=comment
        )

    else:
        text = format_log(
            chat_id=message.chat.id,
            chat_title=message.chat.title,
            user_id=message.from_user.id,
            user_name=message.from_user.full_name,
            user_bio=(await BOT.get_chat(chat_id=message.from_user.id)).bio,
            message_id=message.message_id,
            message_date=message.date.astimezone(
                ZoneInfo(TIMEZONE)
            ).strftime(DATE_FORMAT),
            message_text=message.text,
            comment=comment
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
                f"User bio: {entry['user_bio']}",
                f"Chat ID: {entry['chat_id']}",
                f"Chat title: {entry['chat_title']}",
                f"Message ID: {entry['message_id']}",
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
        if len(line) < 2:
            continue

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


def extract_field(text: str, target: str) -> int | None:
    rows = text.split("\n")

    for row in rows:
        index = row.find(target)
        if index != -1:
            field = row[index+len(target):]
            break
    else:
        return None

    if field == "None":
        return None

    return int(field)


@dispatcher.message(CommandStart())
async def start_message_handler(message: Message):
    await log(message, Text.log_recieved_private)

    if is_trusted:
        menu = await message.answer(
            Text.select_logs,
            reply_markup=get_logs_menu_keyboard()
        )
        await menu.pin()

    else:
        await message.answer(Text.forbidden_command)


@dispatcher.message(F.chat.type == "private")
async def private_message_handler(message: Message) -> None:
    await log(message, Text.log_recieved_private)


@dispatcher.message()
async def message_handler(message: Message) -> None:
    await log(message, Text.log_recieved_public)

    if not is_in_valid_chat(message):
        await log(message, Text.log_unsupported_chat)
        return

    elif is_trusted(message):
        await log(message, Text.log_trusted_user)
        return

    elif await is_valid(message):
        await log(message, Text.log_message_valid)
    else:
        await log(message, Text.log_message_invalid)

        message_id = message.message_id
        chat_id = message.chat.id
        user_id = message.from_user.id

        try:
            await BOT.delete_message(
                chat_id=chat_id,
                message_id=message_id
            )
            await BOT.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id
            )

        except TelegramBadRequest:
            await log(
                message,
                Text.log_f_ban_fail.format(user_id, chat_id, message_id)
            )

        else:
            await log(
                message,
                Text.log_f_ban_success.format(user_id, chat_id, message_id)
            )


@dispatcher.edited_message()
async def edited_message_handler(message: Message) -> None:
    await message_handler(message)


@dispatcher.callback_query(LogsMenuCallback.filter())
async def logs_menu_callback_query_handler(
    callback_query: CallbackQuery,
    callback_data: LogsMenuCallback
):
    unit = callback_data.time_unit
    value = callback_data.time_value

    now = datetime.now()
    delta = timedelta(**{unit: value})
    start = now - delta

    entries = get_log_entries(filename=LOG_PATH, start=start, end=now)

    if entries:
        for entry in entries:
            await callback_query.message.answer(
                entry,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=get_ban_user_keyboard()
            )

    else:
        await callback_query.message.answer(Text.no_data)

    await callback_query.answer()


@dispatcher.callback_query(BanUserCallback.filter())
async def ban_user_callback_query_handler(
    callback_query: CallbackQuery,
    callback_data: BanUserCallback
):
    message = callback_query.message

    message_id = extract_field(message.text, "Message ID: ")
    chat_id = extract_field(message.text, "Chat ID: ")
    user_id = extract_field(message.text, "User ID: ")

    if all((message_id, chat_id, user_id)):
        try:
            await BOT.delete_message(chat_id=chat_id, message_id=message_id)
            await BOT.ban_chat_member(chat_id=chat_id, user_id=user_id)

        except TelegramBadRequest:
            await message.answer(
                Text.f_ban_fail.format(user_id, chat_id, message_id)
            )
            await log(
                message,
                Text.log_f_ban_fail.format(user_id, chat_id, message_id)
            )

        else:
            await message.answer(
                Text.f_ban_success.format(user_id, chat_id, message_id),
                reply_markup=get_unban_user_keyboard()
            )
            await log(
                message,
                Text.log_f_ban_success.format(user_id, chat_id, message_id)
            )

    else:
        await message.answer(
            Text.f_ban_fail.format(user_id, chat_id, message_id)
        )
        await log(message, Text.log_f_ban_fail.format(user_id, chat_id, message_id))

    await callback_query.answer()


@dispatcher.callback_query(UnbanUserCallback.filter())
async def unban_user_callback_query_handler(
    callback_query: CallbackQuery,
    callback_data: UnbanUserCallback
):
    message = callback_query.message

    chat_id = extract_field(message.text, "Chat ID: ")
    user_id = extract_field(message.text, "User ID: ")

    try:
        await BOT.unban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            only_if_banned=True
        )

    except TelegramBadRequest:
        await message.answer(Text.f_unban_fail.format(user_id))
        await log(message, Text.log_f_unban_fail.format(user_id))

    else:
        await message.answer(
            Text.f_unban_success.format(user_id),
            reply_markup=get_ban_user_keyboard()
        )
        await log(message, Text.log_f_unban_success.format(user_id))

    await callback_query.answer()


async def main() -> None:
    global ADMINS
    global BOT

    # Create bot
    BOT = Bot(token=TOKEN)

    # Get admins of all supported chats
    for chat_id in VALID_CHATS:
        admins = await BOT.get_chat_administrators(chat_id=chat_id)
        ADMINS.extend(
            [
                {"name": admin.user.full_name, "id": admin.user.id}
                for admin in admins
            ]
        )

    _admins = ", ".join(
        set([f"{admin['name']} ({admin['id']})" for admin in ADMINS])
    )
    await log(None, f"Current admins: {_admins}")

    # Run events dispatching
    await dispatcher.start_polling(BOT)


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
    LATIN_TO_CYRILLIC: dict[int, str] = str.maketrans(
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

    # Run bot
    asyncio.run(main())
