"""Read-only PostgreSQL business-database sources that feed the CSV ingestion pipeline.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE source_connections (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind = 'postgres'),
            host text NOT NULL,
            port integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
            database text NOT NULL,
            username text NOT NULL,
            schema_name text NOT NULL,
            table_orders text NOT NULL,
            table_products text NOT NULL,
            table_customers text NOT NULL,
            table_returns text NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            last_sync_at timestamptz,
            last_sync_status text CHECK (last_sync_status IN ('succeeded', 'failed')),
            last_sync_error text,
            last_dataset_id uuid REFERENCES datasets (id) ON DELETE SET NULL
        );
        CREATE INDEX source_connections_business_idx
            ON source_connections (business_id, created_at);

        ALTER TABLE datasets ADD COLUMN source_type text NOT NULL DEFAULT 'upload'
            CHECK (source_type IN ('upload', 'postgres'));
        ALTER TABLE datasets ADD COLUMN source_connection_id uuid
            REFERENCES source_connections (id) ON DELETE SET NULL;

        ALTER TABLE source_connections ENABLE ROW LEVEL SECURITY;
        CREATE POLICY source_connections_tenant_select ON source_connections FOR SELECT
            TO {APP_ROLE} USING ({TENANT});
        CREATE POLICY source_connections_tenant_insert ON source_connections FOR INSERT
            TO {APP_ROLE} WITH CHECK ({TENANT});
        CREATE POLICY source_connections_tenant_update ON source_connections FOR UPDATE
            TO {APP_ROLE} USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT SELECT, INSERT ON source_connections TO {APP_ROLE};
        GRANT UPDATE (last_sync_at, last_sync_status, last_sync_error, last_dataset_id)
            ON source_connections TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE datasets DROP COLUMN source_connection_id;
        ALTER TABLE datasets DROP COLUMN source_type;
        DROP TABLE source_connections;
    """)
