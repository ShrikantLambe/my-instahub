"""Opaque feed cursors: the (created_at, id) of the last post the client saw.

Paging by position instead of OFFSET means new posts arriving at the top never shift
the next page, so nothing is skipped or shown twice, and the database can seek
straight to the spot using the (author_id, created_at DESC) index instead of
counting past N rows.
"""

import base64
from datetime import datetime


class BadCursor(ValueError):
    pass


def encode(created_at: datetime, post_id: int) -> str:
    raw = f"{created_at.isoformat()}|{post_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, pid = base64.urlsafe_b64decode(padded.encode()).decode().split("|")
        created_at = datetime.fromisoformat(ts)
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        post_id = int(pid)
        if not 0 < post_id < 2**63:  # a Postgres bigint; bigger would be a database error
            raise ValueError("cursor id out of range")
        return created_at, post_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadCursor("invalid cursor") from exc
