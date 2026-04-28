from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ApiResponse(BaseModel, Generic[T]):
    data: T | None = None
    error: ApiError | None = None
