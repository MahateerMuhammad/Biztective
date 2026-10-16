"""Identity, tenancy, dataset metadata and audit log with RLS and least-privilege grants.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
CURRENT_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
CURRENT_BUSINESS_ID = "NULLIF(current_setting('app.current_business_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                RAISE EXCEPTION 'Database role {APP_ROLE} must exist before migrating '
                    '(see infra/postgres/create-app-role.sh)';
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            email text NOT NULL,
            password_hash text NOT NULL CHECK (length(password_hash) > 0),
            display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT users_email_normalised CHECK (email = lower(btrim(email))),
            CONSTRAINT users_email_length CHECK (length(email) BETWEEN 3 AND 254)
        );
        CREATE UNIQUE INDEX users_email_key ON users (email);

        CREATE TABLE sessions (
            id_hash text PRIMARY KEY CHECK (id_hash ~ '^[0-9a-f]{64}$'),
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            CONSTRAINT sessions_expiry_after_creation CHECK (expires_at > created_at)
        );
        CREATE INDEX sessions_user_id_idx ON sessions (user_id);

        CREATE TABLE auth_attempts (
            id uuid PRIMARY KEY,
            email_hash text NOT NULL,
            ip_hash text NOT NULL,
            succeeded boolean NOT NULL,
            at timestamptz NOT NULL
        );
        CREATE INDEX auth_attempts_email_at_idx ON auth_attempts (email_hash, at);
        CREATE INDEX auth_attempts_ip_at_idx ON auth_attempts (ip_hash, at);

        CREATE TABLE businesses (
            id uuid PRIMARY KEY,
            name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
            currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            timezone text NOT NULL CHECK (length(timezone) BETWEEN 1 AND 64),
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        );

        CREATE TABLE memberships (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN ('owner', 'analyst', 'viewer')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_id, user_id)
        );
        CREATE INDEX memberships_user_id_idx ON memberships (user_id);

        CREATE TABLE datasets (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            version integer NOT NULL CHECK (version > 0),
            status text NOT NULL
                CHECK (status IN ('uploaded', 'validating', 'ready', 'rejected', 'deleted')),
            products_rows integer CHECK (products_rows >= 0),
            customers_rows integer CHECK (customers_rows >= 0),
            sales_rows integer CHECK (sales_rows >= 0),
            returns_rows integer CHECK (returns_rows >= 0),
            date_min date,
            date_max date,
            checksum_sha256 text CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
            uploaded_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT datasets_business_version_key UNIQUE (business_id, version),
            CONSTRAINT datasets_date_range CHECK (date_min IS NULL OR date_max >= date_min)
        );

        -- No foreign keys: audit records must outlive the rows they describe.
        CREATE TABLE audit_log (
            id uuid PRIMARY KEY,
            business_id uuid,
            actor_user_id uuid,
            action text NOT NULL CHECK (length(action) BETWEEN 1 AND 100),
            target_type text,
            target_id text,
            request_id text,
            ip_prefix text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_log_business_id_idx ON audit_log (business_id, id DESC);

        CREATE FUNCTION audit_log_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
        CREATE TRIGGER audit_log_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
    """)

    # Row-level security: tenant data is visible only inside the matching transaction context.
    op.execute(f"""
        ALTER TABLE businesses ENABLE ROW LEVEL SECURITY;
        CREATE POLICY businesses_member_select ON businesses FOR SELECT TO {APP_ROLE}
            USING (EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.business_id = businesses.id AND m.user_id = {CURRENT_USER_ID}
            ));

        ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
        CREATE POLICY memberships_own_select ON memberships FOR SELECT TO {APP_ROLE}
            USING (user_id = {CURRENT_USER_ID});

        ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
        CREATE POLICY datasets_tenant_select ON datasets FOR SELECT TO {APP_ROLE}
            USING (business_id = {CURRENT_BUSINESS_ID});

        ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
        CREATE POLICY audit_log_tenant_select ON audit_log FOR SELECT TO {APP_ROLE}
            USING (business_id = {CURRENT_BUSINESS_ID});
        CREATE POLICY audit_log_insert ON audit_log FOR INSERT TO {APP_ROLE}
            WITH CHECK (business_id IS NULL OR business_id = {CURRENT_BUSINESS_ID});
    """)

    # Least privilege for the runtime role: only the operations the application performs.
    op.execute(f"""
        GRANT SELECT ON alembic_version TO {APP_ROLE};
        GRANT SELECT ON users TO {APP_ROLE};
        GRANT UPDATE (password_hash) ON users TO {APP_ROLE};
        GRANT SELECT, INSERT ON sessions TO {APP_ROLE};
        GRANT UPDATE (last_seen_at, revoked_at) ON sessions TO {APP_ROLE};
        GRANT SELECT, INSERT ON auth_attempts TO {APP_ROLE};
        GRANT SELECT ON businesses, memberships, datasets TO {APP_ROLE};
        GRANT SELECT, INSERT ON audit_log TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
        REVOKE SELECT ON alembic_version FROM {APP_ROLE};
        DROP TABLE audit_log;
        DROP FUNCTION audit_log_append_only();
        DROP TABLE datasets;
        DROP POLICY businesses_member_select ON businesses;  -- references memberships
        DROP TABLE memberships;
        DROP TABLE businesses;
        DROP TABLE auth_attempts;
        DROP TABLE sessions;
        DROP TABLE users;
    """)
