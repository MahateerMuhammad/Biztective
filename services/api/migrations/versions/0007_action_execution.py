"""Integrations and execution of approved plans: verified Google Sheets bindings, one action per
approved plan version and an append-only attempt history (AMD-18).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"
JOBS = ("ingest_dataset", "analyze_dataset", "run_investigation", "run_recovery_plan")
HEX64 = "'^[0-9a-f]{64}$'"


def _list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
            CHECK (type IN ({_list((*JOBS, "execute_action"))}));

        CREATE TABLE integrations (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind = 'google_sheets'),
            status text NOT NULL
                CHECK (status IN ('pending_verification', 'verified', 'revoked')),
            spreadsheet_id text NOT NULL CHECK (spreadsheet_id ~ '^[A-Za-z0-9_-]{{20,100}}$'),
            verify_code_hash text CHECK (verify_code_hash ~ {HEX64}),
            verify_expires_at timestamptz,
            verified_at timestamptz,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT integrations_verified
                CHECK (status <> 'verified' OR verified_at IS NOT NULL)
        );
        -- A spreadsheet is bound to at most one business, and only a proven binding counts:
        -- an unverified request cannot squat a spreadsheet (SEC-17).
        CREATE UNIQUE INDEX integrations_spreadsheet_once ON integrations (spreadsheet_id)
            WHERE status = 'verified';
        CREATE UNIQUE INDEX integrations_one_sheet_per_business ON integrations (business_id)
            WHERE status = 'verified';
        CREATE INDEX integrations_business_idx ON integrations (business_id, created_at);

        CREATE TABLE plan_actions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            plan_id uuid NOT NULL REFERENCES recovery_plans (id) ON DELETE CASCADE,
            plan_version smallint NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ {HEX64}),
            approval_id uuid NOT NULL REFERENCES plan_decisions (id),
            kind text NOT NULL CHECK (kind = 'sheets.write_recovery_tasks'),
            integration_id uuid NOT NULL REFERENCES integrations (id),
            destination jsonb NOT NULL,
            payload jsonb NOT NULL,
            payload_hash text NOT NULL CHECK (payload_hash ~ {HEX64}),
            idempotency_key text NOT NULL CHECK (idempotency_key ~ {HEX64}),
            status text NOT NULL CHECK (status IN ('pending', 'succeeded', 'verified', 'failed',
                'manual_review')),
            attempts smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            error_code text,
            external_ref jsonb,
            verification jsonb,
            requested_by uuid NOT NULL REFERENCES users (id),
            lease_owner uuid,
            lease_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            CONSTRAINT plan_actions_version
                FOREIGN KEY (plan_id, plan_version)
                REFERENCES recovery_plan_versions (plan_id, version),
            CONSTRAINT plan_actions_once UNIQUE (plan_id, plan_version, kind)
        );
        CREATE INDEX plan_actions_business_idx ON plan_actions (business_id, created_at);

        CREATE TABLE action_executions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            action_id uuid NOT NULL REFERENCES plan_actions (id) ON DELETE CASCADE,
            attempt smallint NOT NULL CHECK (attempt >= 1),
            status text NOT NULL
                CHECK (status IN ('started', 'completed', 'failed', 'interrupted')),
            error_code text,
            writes_sent boolean NOT NULL DEFAULT false,
            calls jsonb NOT NULL DEFAULT '[]'::jsonb,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            CONSTRAINT action_executions_attempt UNIQUE (action_id, attempt),
            CONSTRAINT action_executions_finished
                CHECK ((status = 'started') = (finished_at IS NULL))
        );

        -- What was approved and where it goes never change after the intent is recorded.
        CREATE FUNCTION plan_actions_binding_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'status' - 'attempts' - 'error_code' - 'external_ref'
                    - 'verification' - 'lease_owner' - 'lease_expires_at' - 'updated_at'
                    - 'finished_at')
                <> (to_jsonb(OLD) - 'status' - 'attempts' - 'error_code' - 'external_ref'
                    - 'verification' - 'lease_owner' - 'lease_expires_at' - 'updated_at'
                    - 'finished_at') THEN
                RAISE EXCEPTION 'plan action % binding is immutable', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER plan_actions_binding_immutable BEFORE UPDATE ON plan_actions
            FOR EACH ROW EXECUTE FUNCTION plan_actions_binding_immutable();

        -- An attempt is closed exactly once and is history afterwards.
        CREATE FUNCTION action_executions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'started'
                OR (to_jsonb(NEW) - 'status' - 'error_code' - 'writes_sent' - 'calls'
                    - 'finished_at')
                   <> (to_jsonb(OLD) - 'status' - 'error_code' - 'writes_sent' - 'calls'
                    - 'finished_at') THEN
                RAISE EXCEPTION 'action execution % is append-only', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER action_executions_append_only BEFORE UPDATE ON action_executions
            FOR EACH ROW EXECUTE FUNCTION action_executions_append_only();
    """)
    statements = []
    for table in ("integrations", "plan_actions", "action_executions"):
        statements.append(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT TO {APP_ROLE}
                USING ({TENANT});
            CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT TO {APP_ROLE}
                WITH CHECK ({TENANT});
            CREATE POLICY {table}_tenant_update ON {table} FOR UPDATE TO {APP_ROLE}
                USING ({TENANT}) WITH CHECK ({TENANT});
            GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
        """)
    statements.append(f"""
        GRANT UPDATE (status, verify_code_hash, verify_expires_at, verified_at, updated_at)
            ON integrations TO {APP_ROLE};
        GRANT UPDATE (status, attempts, error_code, external_ref, verification, lease_owner,
            lease_expires_at, updated_at, finished_at) ON plan_actions TO {APP_ROLE};
        GRANT UPDATE (status, error_code, writes_sent, calls, finished_at)
            ON action_executions TO {APP_ROLE};
    """)
    op.execute("\n".join(statements))


def downgrade() -> None:
    op.execute(f"""
        DROP TABLE action_executions;
        DROP TABLE plan_actions;
        DROP TABLE integrations;
        DROP FUNCTION action_executions_append_only();
        DROP FUNCTION plan_actions_binding_immutable();
        DELETE FROM jobs WHERE type = 'execute_action';
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN ({_list(JOBS)}));
    """)
