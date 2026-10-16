"""Write the API's OpenAPI document to openapi.json (consumed by apps/web type generation).

No database connection is made: the schema is derived from the route definitions only.
The settings below are throwaway placeholders required to construct the app.
"""

import json
import secrets
import sys
from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

OUTPUT = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> int:
    settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql+psycopg://openapi-export@localhost/none"),
        session_secret=SecretStr(secrets.token_urlsafe(48)),
        frontend_origin="http://localhost:3000",
    )
    schema = create_app(settings).openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(f"Wrote {OUTPUT.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
