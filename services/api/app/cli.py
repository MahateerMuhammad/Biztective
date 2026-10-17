"""Administrative commands. Run as the schema-owner role, never from the web process.

    uv run python -m app.cli seed-demo

Creates or updates the demonstration owner account, its business and the owner membership.
Credentials are read from the environment only (never from source control).
"""

import argparse
import sys
import uuid
from typing import Literal
from zoneinfo import available_timezones

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session as OrmSession

from app.auth.passwords import hash_password, password_policy_errors
from app.auth.service import normalise_email
from app.models import AuditLog, Business, Membership, User


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url_migrations: SecretStr
    demo_owner_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    demo_owner_password: SecretStr
    demo_owner_name: str = Field(default="Owner", min_length=1, max_length=100)
    demo_business_name: str = Field(min_length=1, max_length=120)
    demo_business_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    demo_business_timezone: str = "UTC"

    @field_validator("demo_business_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("DEMO_BUSINESS_TIMEZONE must be an IANA time zone name")
        return value

    @model_validator(mode="after")
    def _password_policy(self) -> SeedSettings:
        errors = password_policy_errors(
            self.demo_owner_password.get_secret_value(), self.demo_owner_email
        )
        if errors:
            raise ValueError(" ".join(errors))
        return self


def seed_demo(settings: SeedSettings) -> Literal["created", "updated"]:
    email = normalise_email(settings.demo_owner_email)
    password_hash = hash_password(settings.demo_owner_password.get_secret_value())
    engine = create_engine(settings.database_url_migrations.get_secret_value())
    try:
        with OrmSession(engine) as session, session.begin():
            user = session.scalar(select(User).where(User.email == email))
            outcome: Literal["created", "updated"] = "updated" if user else "created"
            if user is None:
                user = User(email=email, password_hash=password_hash, display_name="")
                session.add(user)
            user.password_hash = password_hash
            user.display_name = settings.demo_owner_name
            user.is_active = True
            session.flush()

            business = session.scalar(
                select(Business).where(
                    Business.created_by == user.id,
                    Business.name == settings.demo_business_name,
                    Business.deleted_at.is_(None),
                )
            )
            if business is None:
                business = Business(
                    name=settings.demo_business_name,
                    currency=settings.demo_business_currency,
                    timezone=settings.demo_business_timezone,
                    created_by=user.id,
                )
                session.add(business)
                session.flush()

            membership = session.get(Membership, (business.id, user.id))
            if membership is None:
                session.add(Membership(business_id=business.id, user_id=user.id, role="owner"))
            else:
                session.execute(
                    update(Membership)
                    .where(Membership.business_id == business.id, Membership.user_id == user.id)
                    .values(role="owner")
                )
            session.execute(
                insert(AuditLog).values(
                    id=uuid.uuid7(),
                    business_id=business.id,
                    actor_user_id=None,
                    action="seed.demo_account",
                    target_type="user",
                    target_id=str(user.id),
                    metadata_={"outcome": outcome},
                )
            )
    finally:
        engine.dispose()
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="Create or update the demonstration account")
    parser.parse_args(argv)

    try:
        settings = SeedSettings()
    except ValidationError as exc:
        fields = ", ".join(str(err["loc"][0]).upper() for err in exc.errors() if err["loc"])
        sys.stderr.write(f"Invalid or missing seed configuration: {fields or 'see errors'}\n")
        for err in exc.errors():
            if not err["loc"]:
                sys.stderr.write(f"- {err['msg']}\n")
        return 2
    outcome = seed_demo(settings)
    sys.stdout.write(f"Demo account {outcome}: {normalise_email(settings.demo_owner_email)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
