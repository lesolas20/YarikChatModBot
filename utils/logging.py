import logging
from datetime import datetime

LOG_PATH: str = "logfile.log"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S.%f%:z"


class LoggingFormatter(logging.Formatter):
    """Override logging.Formatter to use aware datetime objects."""

    def formatTime(self, record, datefmt=None):  # noqa: N802
        dt = datetime.fromtimestamp(record.created).astimezone()

        if datefmt:
            return dt.strftime(datefmt)

        return dt.isoformat(timespec="milliseconds")


def setup() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    formatter = LoggingFormatter(
        "%(asctime)s - %(name)s - %(message)s",
        datefmt=LOG_DATE_FORMAT,
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
