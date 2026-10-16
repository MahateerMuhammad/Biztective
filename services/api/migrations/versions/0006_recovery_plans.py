"""Recovery plans: immutable hashed plan versions, an append-only decision history and the
slice-economics evidence they are computed from (AMD-17).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"
KINDS = (
    "incident_detection",
    "source_records",
    "return_reason_mix",
    "metric_breakdown",
    "period_comparison",
    "return_investigation",
    "segment_analysis",
    "customer_exposure",
    "data_quality_check",
    "margin_bridge",
    "human_context",
)
JOBS = ("ingest_dataset", "analyze_dataset", "run_investigation")
HEX64 = "'^[0-9a-f]{64}$'"


def _list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_list((*KINDS, "slice_economics"))}));
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
            CHECK (type IN ({_list((*JOBS, "run_recovery_plan"))}));

        CREATE TABLE recovery_plans (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            investigation_version smallint NOT NULL CHECK (investigation_version >= 1),
            status text NOT NULL CHECK (status IN ('queued', 'running', 'awaiting_decision',
                'approved', 'rejected', 'failed')),
            current_version smallint NOT NULL DEFAULT 0 CHECK (current_version >= 0),
            approved_version smallint CHECK (approved_version >= 1),
            initial_constraints jsonb NOT NULL,
            graph_version text NOT NULL,
            catalog_version text NOT NULL,
            error_code text,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT recovery_plans_approved_current
                CHECK (approved_version IS NULL OR approved_version = current_version)
        );
        -- One plan in progress or approved per investigation; a rejected or failed plan can
        -- be followed by a new one.
        CREATE UNIQUE INDEX recovery_plans_open ON recovery_plans (investigation_id)
            WHERE status IN ('queued', 'running', 'awaiting_decision', 'approved');
        CREATE INDEX recovery_plans_business_idx ON recovery_plans (business_id, created_at);

        CREATE TABLE recovery_plan_versions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            plan_id uuid NOT NULL REFERENCES recovery_plans (id) ON DELETE CASCADE,
            version smallint NOT NULL CHECK (version >= 1),
            content jsonb NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ {HEX64}),
            valid boolean NOT NULL,
            validation jsonb NOT NULL,
            caused_by_decision_id uuid,
            created_by uuid REFERENCES users (id),
            checkpoint_id text CHECK (char_length(checkpoint_id) BETWEEN 1 AND 64),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT recovery_plan_versions_key UNIQUE (plan_id, version),
            CONSTRAINT recovery_plan_versions_cause
                CHECK ((version = 1) = (caused_by_decision_id IS NULL))
        );

        CREATE TABLE plan_decisions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            plan_id uuid NOT NULL REFERENCES recovery_plans (id) ON DELETE CASCADE,
            version smallint NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ {HEX64}),
            decision text NOT NULL
                CHECK (decision IN ('approve', 'reject', 'request_changes', 'revise')),
            decided_by uuid NOT NULL REFERENCES users (id),
            decided_role text NOT NULL CHECK (decided_role IN ('owner', 'analyst')),
            reason text CHECK (char_length(reason) BETWEEN 1 AND 1000),
            edits jsonb,
            checkpoint_id text CHECK (char_length(checkpoint_id) BETWEEN 1 AND 64),
            created_at timestamptz NOT NULL DEFAULT now(),
            applied_at timestamptz,
            outcome text CHECK (outcome IN ('applied', 'refused')),
            refusal text,
            CONSTRAINT plan_decisions_version
                FOREIGN KEY (plan_id, version) REFERENCES recovery_plan_versions (plan_id, version),
            CONSTRAINT plan_decisions_edits
                CHECK ((decision IN ('request_changes', 'revise')) = (edits IS NOT NULL)),
            CONSTRAINT plan_decisions_approver
                CHECK (decision <> 'approve' OR decided_role = 'owner'),
            CONSTRAINT plan_decisions_outcome CHECK ((applied_at IS NULL) = (outcome IS NULL))
        );
        -- One decision per paused version, and one revision of an approved version: duplicate
        -- and concurrent submissions have exactly one effect.
        CREATE UNIQUE INDEX plan_decisions_once ON plan_decisions (plan_id, version)
            WHERE decision <> 'revise';
        CREATE UNIQUE INDEX plan_decisions_revise_once ON plan_decisions (plan_id, version)
            WHERE decision = 'revise';
        ALTER TABLE recovery_plan_versions ADD CONSTRAINT recovery_plan_versions_decision
            FOREIGN KEY (caused_by_decision_id) REFERENCES plan_decisions (id);

        -- Versions are immutable; only the paused checkpoint is recorded, once.
        CREATE FUNCTION recovery_plan_versions_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.checkpoint_id IS NOT NULL
                OR (to_jsonb(NEW) - 'checkpoint_id') <> (to_jsonb(OLD) - 'checkpoint_id') THEN
                RAISE EXCEPTION 'plan version % is immutable', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER recovery_plan_versions_immutable BEFORE UPDATE ON recovery_plan_versions
            FOR EACH ROW EXECUTE FUNCTION recovery_plan_versions_immutable();

        -- Decisions are append-only history; the workflow records its outcome exactly once.
        CREATE FUNCTION plan_decisions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.applied_at IS NOT NULL
                OR (to_jsonb(NEW) - 'applied_at' - 'outcome' - 'refusal')
                    <> (to_jsonb(OLD) - 'applied_at' - 'outcome' - 'refusal') THEN
                RAISE EXCEPTION 'plan decision % is append-only', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER plan_decisions_append_only BEFORE UPDATE ON plan_decisions
            FOR EACH ROW EXECUTE FUNCTION plan_decisions_append_only();
    """)
    statements = []
    for table in ("recovery_plans", "recovery_plan_versions", "plan_decisions"):
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
        GRANT UPDATE (status, current_version, approved_version, error_code, updated_at)
            ON recovery_plans TO {APP_ROLE};
        GRANT UPDATE (checkpoint_id) ON recovery_plan_versions TO {APP_ROLE};
        GRANT UPDATE (applied_at, outcome, refusal) ON plan_decisions TO {APP_ROLE};
    """)
    op.execute("\n".join(statements))


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE recovery_plan_versions DROP CONSTRAINT recovery_plan_versions_decision;
        DROP TABLE plan_decisions;
        DROP TABLE recovery_plan_versions;
        DROP TABLE recovery_plans;
        DROP FUNCTION plan_decisions_append_only();
        DROP FUNCTION recovery_plan_versions_immutable();
        DELETE FROM jobs WHERE type = 'run_recovery_plan';
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN ({_list(JOBS)}));
        DELETE FROM evidence_items WHERE kind = 'slice_economics';
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_list(KINDS)}));
    """)
