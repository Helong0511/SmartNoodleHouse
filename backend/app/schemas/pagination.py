"""分页参数统一约束"""
from pydantic import BaseModel, Field


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=20, ge=1, le=100)


def clamp_page(page: int = 1, page_size: int = 20, *, max_size: int = 100) -> tuple[int, int]:
    page = max(1, min(int(page or 1), 100000))
    page_size = max(1, min(int(page_size or 20), max_size))
    return page, page_size
