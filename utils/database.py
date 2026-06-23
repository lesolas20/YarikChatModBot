import sqlite3
from os import PathLike
from datetime import datetime


class Database:
    def __init__(
        self,
        file: str | bytes | PathLike[str] | PathLike[bytes],
    ) -> None:
        sqlite3.register_adapter(datetime, self._adapt_datetime)
        sqlite3.register_converter("datetime", self._convert_datetime)

        connection = sqlite3.connect(
            file,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        connection.row_factory = sqlite3.Row
        self._cursor = connection.cursor()

    def close(self) -> None:
        """Close the database connection."""
        self._cursor.close()
        self._cursor.connection.close()

    def get_user_first_seen(self, user_id: int) -> datetime | None:
        """Get the `datetime` of the first recorded iteraction for a
        user with id of `user_id`. Return `None` if no iteraction has
        been recorded.
        """
        self._cursor.execute(
            "SELECT first_seen FROM users WHERE id = ?",
            (user_id,),
        )
        result: sqlite3.Row | None = self._cursor.fetchone()

        if result is None:
            return None

        return result["first_seen"]

    def get_user_violations(self, user_id: int) -> int | None:
        """Get the number of violations for a user with id of `user_id`.
        Return `None` if no entry for this user exists.
        """
        self._cursor.execute(
            "SELECT violations FROM users WHERE id = ?",
            (user_id,),
        )
        result: sqlite3.Row | None = self._cursor.fetchone()

        if result is None:
            return None

        return result["violations"]

    def user_exists(self, user_id: int) -> bool:
        """Return `True` if a user with id of `user_id` exists in the
        database, return `False` otherwise."""
        self._cursor.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        )
        result: sqlite3.Row | None = self._cursor.fetchone()

        return result is not None

    def insert_or_update_user(
        self,
        user_id: int,
        first_seen: datetime,
        violations: int,
    ) -> None:
        """Insert a new user entry if it does not exist, update the
        existing entry otherwise."""
        self._cursor.execute(
            "INSERT INTO users (id, first_seen, violations)"
            "VALUES (?, ?, ?)"
            "ON CONFLICT (id)"
            "DO UPDATE SET violations = ?",
            (user_id, first_seen, violations, violations),
        )
        self._cursor.connection.commit()

    @staticmethod
    def _adapt_datetime(value: datetime) -> str:
        """Adapt a `datetime.datetime` object to an ISO 8601 date."""
        return value.isoformat(sep=" ", timespec="seconds")

    @staticmethod
    def _convert_datetime(value: bytes) -> datetime:
        """Convert an ISO 8601 datetime to a `datetime.datetime` object."""  # noqa: W505
        return datetime.fromisoformat(value.decode())
