from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.tenancy.permissions import Role


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class LoginRequest(StrictModel):
    email: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$"
        ),
    ]
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class CsrfResponse(BaseModel):
    csrf_token: str


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str


class MembershipOut(BaseModel):
    business_id: UUID
    business_name: str
    role: Role


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
