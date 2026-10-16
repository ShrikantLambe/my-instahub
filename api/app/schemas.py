import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from asyncpg import Record
from pydantic import BaseModel, Field, field_validator

from app import storage

USERNAME_RE = re.compile(r"^[a-z0-9_.]{3,30}$")


def avatar_url(key: str | None) -> str | None:
    return storage.public_url(key) if key else None


class Author(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    avatar_url: str | None = None

    @classmethod
    def from_row(cls, row: Record) -> "Author":
        """From a row with author_id / author_username / ... columns."""
        return cls(
            id=row["author_id"],
            username=row["author_username"],
            display_name=row["author_display_name"],
            avatar_url=avatar_url(row["author_avatar_key"]),
        )


class Post(BaseModel):
    id: int
    author: Author
    caption: str | None
    image_key: str
    image_url: str
    image_width: int | None
    image_height: int | None
    like_count: int
    like_version: int  # bumps with every change to like_count; lets copies refuse stale values
    viewer_has_liked: bool = False  # always False for anonymous viewers
    comment_count: int = 0
    comment_version: int = 0  # like like_version, for the comment count
    created_at: datetime

    @classmethod
    def from_row(cls, row: Record) -> "Post":
        return cls(
            id=row["id"],
            author=Author.from_row(row),
            caption=row["caption"],
            image_key=row["image_key"],
            image_url=storage.public_url(row["image_key"]),
            image_width=row["image_width"],
            image_height=row["image_height"],
            like_count=row["like_count"],
            like_version=row["like_version"],
            comment_count=row["comment_count"],
            comment_version=row["comment_version"],
            created_at=row["created_at"],
        )


class User(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    avatar_key: str | None
    avatar_url: str | None = None
    bio: str | None = None
    city: str | None = None
    website: str | None = None
    created_at: datetime
    post_count: int
    follower_count: int
    following_count: int
    viewer_follows: bool = False  # always False for anonymous viewers


class Profile(BaseModel):
    """The signed-in user's own profile (GET/PUT/PATCH /me)."""

    id: UUID
    username: str
    display_name: str | None
    avatar_key: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    city: str | None = None
    website: str | None = None

    @classmethod
    def from_row(cls, row: Record) -> "Profile":
        return cls(**dict(row), avatar_url=avatar_url(row["avatar_key"]))


def _username(v: str) -> str:
    v = v.strip().lower()
    if not USERNAME_RE.fullmatch(v):
        raise ValueError("use 3-30 letters, numbers, dots or underscores")
    return v


def _optional_text(v: str | None, limit: int) -> str | None:
    """Trim; blank means "clear it"."""
    if v is None:
        return None
    v = v.strip()
    if len(v) > limit:
        raise ValueError(f"keep it under {limit} characters")
    return v or None


class ProfileIn(BaseModel):
    username: str
    display_name: str | None = Field(default=None, max_length=50)

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _username(v)


class ProfileUpdate(BaseModel):
    """PATCH /me: only the fields sent are changed; null or "" clears a field."""

    username: str | None = None
    display_name: str | None = None
    bio: str | None = None
    city: str | None = None
    website: str | None = None
    avatar_key: str | None = None  # from POST /uploads with purpose "avatar"; null removes it

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("username can't be removed")
        return _username(v)

    @field_validator("display_name")
    @classmethod
    def check_display_name(cls, v: str | None) -> str | None:
        return _optional_text(v, 50)

    @field_validator("bio")
    @classmethod
    def check_bio(cls, v: str | None) -> str | None:
        v = _optional_text(v, 150)
        if v and v.count("\n") > 4:
            raise ValueError("use at most 5 lines")
        return v

    @field_validator("city")
    @classmethod
    def check_city(cls, v: str | None) -> str | None:
        v = _optional_text(v, 60)
        return " ".join(v.split()) if v else v

    @field_validator("website")
    @classmethod
    def check_website(cls, v: str | None) -> str | None:
        """A plain http(s) link; "example.com" becomes "https://example.com". Anything
        else (javascript:, data:, ...) is refused, since it ends up in an href."""
        v = _optional_text(v, 200)
        if not v:
            return v
        if "://" not in v:
            v = "https://" + v
        parts = urlsplit(v)
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https") or "." not in host or " " in v:
            raise ValueError("enter a web address like example.com")
        return v


class UploadIn(BaseModel):
    content_type: str
    purpose: Literal["post", "avatar"] = "post"

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
    like_version: int


class LikeCountOut(BaseModel):
    post_id: int
    like_count: int
    like_version: int


class FollowOut(BaseModel):
    username: str
    following: bool
    follower_count: int


class FeedPage(BaseModel):
    posts: list[Post]
    next_cursor: str | None  # pass back as ?before= to get the next (older) page


class Comment(BaseModel):
    id: int
    post_id: int
    author: Author
    body: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: Record) -> "Comment":
        return cls(
            id=row["id"],
            post_id=row["post_id"],
            author=Author.from_row(row),
            body=row["body"],
            created_at=row["created_at"],
        )


class CommentIn(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def check_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("write something first")
        if len(v) > 2200:  # Instagram's limit
            raise ValueError("keep it under 2200 characters")
        return v


class CommentPage(BaseModel):
    comments: list[Comment]  # newest first
    next_cursor: str | None  # pass back as ?before= for older comments
    comment_count: int
    comment_version: int


class CommentChange(BaseModel):
    """Result of adding or deleting a comment, plus the post's new comment count."""

    post_id: int
    comment: Comment | None = None  # the new comment (None after a delete)
    deleted_id: int | None = None
    comment_count: int
    comment_version: int
