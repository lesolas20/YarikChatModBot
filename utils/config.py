import json
import logging
from os import getenv
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv

from utils.misc import normalize_text


@dataclass(frozen=True)
class Config:
    bot_token: str
    banned_phrases: list[str]
    valid_chats: list[int]


def load_config() -> Config:
    logger = logging.getLogger(__name__)

    load_dotenv()

    bot_token: str | None = getenv("TOKEN")
    if bot_token is None:
        logger.critical("No bot token found, aborting")
        raise Exception("No bot token found, aborting")

    with Path("banned_phrases.json").open() as file:
        file_text = file.read()
    banned_phrases = [
        normalize_text(phrase) for phrase in json.loads(file_text)
    ]

    with Path("valid_chats.json").open() as file:
        file_text = file.read()
    valid_chats = json.loads(file_text)

    return Config(
        bot_token=bot_token,
        banned_phrases=banned_phrases,
        valid_chats=valid_chats,
    )
