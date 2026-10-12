import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field


class Post(BaseModel):
    post_id: str
    author: str = ""
    created_at: str | None = None
    time_label: str | None = None
    like_count: int = 0
    comment_count: int = 0
    text: str = ""
    image_urls: list[str] = Field(default_factory=list)
    permalink: str | None = None
    id_unstable: bool = False
    raw: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude={"raw"})


class Comment(BaseModel):
    """Một bình luận đã lấy từ trang bài viết."""

    comment_id: str
    post_id: str = ""
    parent_id: str | None = None
    depth: int = 0
    author: str = ""
    text: str = ""
    created_at: str = ""
    like_count: int = 0

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump()


def content_hash(post: Post) -> str:
    payload = {
        "text": post.text,
        "like_count": post.like_count,
        "comment_count": post.comment_count,
        "image_urls": sorted(post.image_urls),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
