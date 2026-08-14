from uuid import UUID

from pydantic import BaseModel, Field


class UserListItem(BaseModel):
    id: UUID
    username: str
    display_name: str
    is_active: bool
    must_change_password: bool
    roles: list[str]


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int
    page: int
    page_size: int


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    role_names: list[str] = Field(min_length=1)


class CreateUserResponse(BaseModel):
    user: UserListItem
    temporary_password: str


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    is_active: bool | None = None


class ResetPasswordResponse(BaseModel):
    temporary_password: str


class ReplaceRolesRequest(BaseModel):
    role_names: list[str] = Field(min_length=1)
