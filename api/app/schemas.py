from datetime import datetime
from uuid import UUID

from asyncpg import Record
from pydantic import BaseModel


class Author(BaseModel):
    id: UUID
    username: str
    display_name: str | None


class Post(BaseModel):
    id: int
    author: Author
    caption: str | None
    image_key: str
    like_count: int
    created_at: datetime

    @classmethod
    def from_row(cls, row: Record) -> "Post":
        return cls(
            id=row["id"],
            author=Author(
                id=row["author_id"],
                username=row["author_username"],
                display_name=row["author_display_name"],
            ),
            caption=row["caption"],
            image_key=row["image_key"],
            like_count=row["like_count"],
            created_at=row["created_at"],
        )


class User(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    avatar_key: str | None
    created_at: datetime
    post_count: int
    follower_count: int
    following_count: int
