import re
from datetime import datetime
from uuid import UUID

from asyncpg import Record
from pydantic import BaseModel, Field, field_validator

from app import storage

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")


class Author(BaseModel):
    id: UUID
    username: str
    display_name: str | None


class Post(BaseModel):
    id: int
    author: Author
    caption: str | None
    image_key: str
    image_url: str
    image_width: int | None
    image_height: int | None
    like_count: int
    viewer_has_liked: bool = False  # always False for anonymous viewers
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
            image_url=storage.public_url(row["image_key"]),
            image_width=row["image_width"],
            image_height=row["image_height"],
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
    viewer_follows: bool = False  # always False for anonymous viewers


class Profile(BaseModel):
    id: UUID
    username: str
    display_name: str | None


class ProfileIn(BaseModel):
    username: str
    display_name: str | None = Field(default=None, max_length=50)

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not USERNAME_RE.fullmatch(v):
            raise ValueError("use 3-30 letters, numbers, dots or underscores")
        return v


class UploadIn(BaseModel):
    content_type: str

    @field_validator("content_type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in storage.IMAGE_TYPES:
            raise ValueError(f"must be one of {', '.join(storage.IMAGE_TYPES)}")
        return v


class UploadOut(BaseModel):
    upload_url: str
    key: str
    expires_in: int


class PostIn(BaseModel):
    image_key: str
    caption: str | None = Field(default=None, max_length=2200)  # Instagram's limit
    # Measured by the browser before upload; optional so older clients still work.
    image_width: int | None = Field(default=None, ge=1, le=20000)
    image_height: int | None = Field(default=None, ge=1, le=20000)


class LikeOut(BaseModel):
    post_id: int
    liked: bool
    like_count: int


class FollowOut(BaseModel):
    username: str
    following: bool
    follower_count: int


class FeedPage(BaseModel):
    posts: list[Post]
    next_cursor: str | None  # pass back as ?before= to get the next (older) page
