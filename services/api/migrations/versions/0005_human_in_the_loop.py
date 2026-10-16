"""Human-in-the-loop clarifications: paused investigations, answers as attributed human
inputs, human-context evidence, evaluation history and interpretation versions (AMD-15).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"
OLD_KINDS = (
    "incident_detection",
    "source_records",
    "return_reason_mix",
    "metric_breakdown",
    "period_comparison",
    "return_investigation",
    "segment_analysis",
    "customer_exposure",
    "data_quality_check",
)
NEW_KINDS = ("margin_bridge", "human_context")
HEX64 = "'^[0-9a-f]{64}$'"


def _kinds(kinds: Sequence[str]) -> str:
    return ", ".join(f"'{k}'" for k in kinds)


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE investigations DROP CONSTRAINT investigations_status_check;
        ALTER TABLE investigations ADD CONSTRAINT investigations_status_check
            CHECK (status IN ('queued', 'running', 'awaiting_human', 'completed',
                'completed_no_incidents', 'insufficient_data', 'failed'));
        -- Start of the current active run segment: the LLM deadline excludes human waiting.
        ALTER TABLE investigations ADD COLUMN run_started_at timestamptz;
        GRANT UPDATE (run_started_at) ON investigations TO {APP_ROLE};

        ALTER TABLE investigation_events ADD COLUMN actor_user_id uuid REFERENCES users (id);

        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_kinds(OLD_KINDS + NEW_KINDS)}));

        ALTER TABLE investigation_evidence
            DROP CONSTRAINT investigation_evidence_requested_by_check;
        ALTER TABLE investigation_evidence ADD CONSTRAINT investigation_evidence_requested_by_check
            CHECK (requested_by IN ('detection', 'system', 'llm', 'human'));

        ALTER TABLE hypotheses DROP CONSTRAINT hypotheses_source_check;
        ALTER TABLE hypotheses ADD CONSTRAINT hypotheses_source_check
            CHECK (source IN ('catalog', 'llm_prioritized', 'human_context'));

        -- Evaluations become an insert-only history keyed by what they were computed from.
        ALTER TABLE hypothesis_evaluations DROP CONSTRAINT hypothesis_evaluations_once;
        ALTER TABLE hypothesis_evaluations
            ADD COLUMN inputs_hash text CHECK (inputs_hash ~ {HEX64}),
            ADD COLUMN interpretation text CHECK (interpretation IN ('intended', 'unintended')),
            ADD COLUMN conflicts jsonb NOT NULL DEFAULT '[]'::jsonb;
        UPDATE hypothesis_evaluations
            SET inputs_hash = encode(sha256(convert_to(hypothesis_id::text, 'UTF8')), 'hex');
        ALTER TABLE hypothesis_evaluations ALTER COLUMN inputs_hash SET NOT NULL;
        ALTER TABLE hypothesis_evaluations ADD CONSTRAINT hypothesis_evaluations_inputs
            UNIQUE (hypothesis_id, inputs_hash);

        ALTER TABLE clarifications DROP CONSTRAINT clarifications_status_check;
        ALTER TABLE clarifications ADD CONSTRAINT clarifications_status_check
            CHECK (status IN ('open', 'awaiting', 'answered', 'applied'));
        ALTER TABLE clarifications DROP CONSTRAINT clarifications_trigger_check;
        ALTER TABLE clarifications ADD CONSTRAINT clarifications_trigger_check
            CHECK (trigger IN ('CT-1', 'CT-2', 'CT-3', 'CT-4', 'CT-5'));
        ALTER TABLE clarifications
            ADD COLUMN response_schema jsonb,
            ADD COLUMN checkpoint_id text CHECK (char_length(checkpoint_id) BETWEEN 1 AND 64),
            ADD COLUMN interrupt_id text CHECK (char_length(interrupt_id) BETWEEN 1 AND 64),
            ADD COLUMN asked_at timestamptz,
            ADD COLUMN answered_at timestamptz,
            ADD COLUMN applied_at timestamptz;
        CREATE POLICY clarifications_tenant_update ON clarifications FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (status, checkpoint_id, interrupt_id, asked_at, answered_at, applied_at)
            ON clarifications TO {APP_ROLE};

        -- Lifecycle moves forward only, and the paused checkpoint is fixed once recorded.
        CREATE FUNCTION clarifications_forward_only() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            ranks CONSTANT text[] := ARRAY['open', 'awaiting', 'answered', 'applied'];
        BEGIN
            IF array_position(ranks, NEW.status) < array_position(ranks, OLD.status) THEN
                RAISE EXCEPTION 'clarification % cannot move from % to %',
                    OLD.id, OLD.status, NEW.status;
            END IF;
            IF OLD.checkpoint_id IS NOT NULL
                AND NEW.checkpoint_id IS DISTINCT FROM OLD.checkpoint_id THEN
                RAISE EXCEPTION 'clarification % checkpoint is immutable', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER clarifications_forward_only BEFORE UPDATE ON clarifications
            FOR EACH ROW EXECUTE FUNCTION clarifications_forward_only();

        CREATE TABLE human_inputs (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            clarification_id uuid NOT NULL REFERENCES clarifications (id) ON DELETE CASCADE,
            kind text NOT NULL DEFAULT 'context' CHECK (kind IN ('context')),
            criterion_key text NOT NULL,
            option_id text CHECK (option_id ~ '^[a-z_]{{1,40}}$'),
            effect text NOT NULL
                CHECK (effect IN ('confirm', 'deny', 'intended', 'unsure', 'unstructured')),
            free_text text CHECK (char_length(free_text) BETWEEN 1 AND 1000),
            checkpoint_id text NOT NULL,
            answered_by uuid NOT NULL REFERENCES users (id),
            answered_at timestamptz NOT NULL DEFAULT now(),
            applied_at timestamptz,
            CONSTRAINT human_inputs_once UNIQUE (clarification_id),
            CONSTRAINT human_inputs_content CHECK (option_id IS NOT NULL OR free_text IS NOT NULL)
        );
        CREATE INDEX human_inputs_investigation_idx ON human_inputs (investigation_id);

        CREATE FUNCTION human_inputs_applied_once() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.applied_at IS NOT NULL THEN
                RAISE EXCEPTION 'human input % was already applied', OLD.id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER human_inputs_applied_once BEFORE UPDATE ON human_inputs
            FOR EACH ROW EXECUTE FUNCTION human_inputs_applied_once();

        CREATE TABLE investigation_versions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            version smallint NOT NULL CHECK (version >= 1),
            findings_id uuid NOT NULL REFERENCES findings (id) ON DELETE CASCADE,
            caused_by_input_id uuid REFERENCES human_inputs (id) ON DELETE CASCADE,
            snapshot jsonb NOT NULL,
            snapshot_hash text NOT NULL CHECK (snapshot_hash ~ {HEX64}),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT investigation_versions_key UNIQUE (investigation_id, version),
            CONSTRAINT investigation_versions_cause
                CHECK ((version = 1) = (caused_by_input_id IS NULL))
        );
    """)

    statements = []
    for table in ("human_inputs", "investigation_versions"):
        statements.append(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT TO {APP_ROLE}
                USING ({TENANT});
            CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT TO {APP_ROLE}
                WITH CHECK ({TENANT});
            GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
        """)
    statements.append(f"""
        CREATE POLICY human_inputs_tenant_update ON human_inputs FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (applied_at) ON human_inputs TO {APP_ROLE};
    """)
    op.execute("\n".join(statements))


def downgrade() -> None:
    op.execute(f"""
        DROP TABLE investigation_versions;
        DROP TABLE human_inputs;
        DROP FUNCTION human_inputs_applied_once();

        DROP TRIGGER clarifications_forward_only ON clarifications;
        DROP FUNCTION clarifications_forward_only();
        REVOKE UPDATE (status, checkpoint_id, interrupt_id, asked_at, answered_at, applied_at)
            ON clarifications FROM {APP_ROLE};
        DROP POLICY clarifications_tenant_update ON clarifications;
        DELETE FROM clarifications WHERE trigger = 'CT-5';
        ALTER TABLE clarifications DROP CONSTRAINT clarifications_trigger_check;
        ALTER TABLE clarifications ADD CONSTRAINT clarifications_trigger_check
            CHECK (trigger IN ('CT-1', 'CT-2', 'CT-3', 'CT-4'));
        UPDATE clarifications SET status = 'open';
        ALTER TABLE clarifications DROP CONSTRAINT clarifications_status_check;
        ALTER TABLE clarifications ADD CONSTRAINT clarifications_status_check
            CHECK (status IN ('open'));
        ALTER TABLE clarifications DROP COLUMN response_schema, DROP COLUMN checkpoint_id,
            DROP COLUMN interrupt_id, DROP COLUMN asked_at, DROP COLUMN answered_at,
            DROP COLUMN applied_at;

        -- Keep each hypothesis's first evaluation (the 1.0.0 shape).
        DELETE FROM hypothesis_evaluations e USING hypothesis_evaluations earlier
            WHERE earlier.hypothesis_id = e.hypothesis_id
              AND (earlier.evaluated_at, earlier.id) < (e.evaluated_at, e.id);
        ALTER TABLE hypothesis_evaluations DROP CONSTRAINT hypothesis_evaluations_inputs;
        ALTER TABLE hypothesis_evaluations DROP COLUMN inputs_hash, DROP COLUMN interpretation,
            DROP COLUMN conflicts;
        ALTER TABLE hypothesis_evaluations ADD CONSTRAINT hypothesis_evaluations_once
            UNIQUE (hypothesis_id);

        DELETE FROM hypotheses WHERE source = 'human_context';
        ALTER TABLE hypotheses DROP CONSTRAINT hypotheses_source_check;
        ALTER TABLE hypotheses ADD CONSTRAINT hypotheses_source_check
            CHECK (source IN ('catalog', 'llm_prioritized'));

        DELETE FROM evidence_items WHERE kind IN ({_kinds(NEW_KINDS)});
        ALTER TABLE investigation_evidence
            DROP CONSTRAINT investigation_evidence_requested_by_check;
        ALTER TABLE investigation_evidence ADD CONSTRAINT investigation_evidence_requested_by_check
            CHECK (requested_by IN ('detection', 'system', 'llm'));
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_kinds(OLD_KINDS)}));

        ALTER TABLE investigation_events DROP COLUMN actor_user_id;

        UPDATE investigations SET status = 'failed', error_code = 'downgraded'
            WHERE status = 'awaiting_human';
        REVOKE UPDATE (run_started_at) ON investigations FROM {APP_ROLE};
        ALTER TABLE investigations DROP COLUMN run_started_at;
        ALTER TABLE investigations DROP CONSTRAINT investigations_status_check;
        ALTER TABLE investigations ADD CONSTRAINT investigations_status_check
            CHECK (status IN ('queued', 'running', 'completed', 'completed_no_incidents',
                'insufficient_data', 'failed'));
    """)
