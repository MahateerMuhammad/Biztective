"""Investigations: graph runs, events, hypotheses, evaluations, tool evidence, LLM calls,
open context requests, findings, and the LangGraph checkpoint schema (AMD-12).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"
INSERT_ONLY = (
    "investigation_events",
    "hypotheses",
    "hypothesis_evaluations",
    "investigation_evidence",
    "clarifications",
    "findings",
    "llm_calls",
)
DETECTION_KINDS = ("incident_detection", "source_records", "return_reason_mix")
TOOL_KINDS = (
    "metric_breakdown",
    "period_comparison",
    "return_investigation",
    "segment_analysis",
    "customer_exposure",
    "data_quality_check",
)

# The checkpoint DDL of langgraph-checkpoint-postgres 3.2.0 (its MIGRATIONS list, versions
# 0-9), frozen here and qualified with the `langgraph` schema so the library never runs DDL
# at runtime. Indexes are built in this transaction (the tables are new and empty), so the
# library's CONCURRENTLY is unnecessary. tests/integration/test_health_and_migrations.py
# fails if the installed library expects a different number of migrations.
CHECKPOINT_MIGRATIONS = 10
CHECKPOINT_DDL = """
    CREATE SCHEMA langgraph;
    CREATE TABLE langgraph.checkpoint_migrations (v INTEGER PRIMARY KEY);
    CREATE TABLE langgraph.checkpoints (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        parent_checkpoint_id TEXT,
        type TEXT,
        checkpoint JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    );
    CREATE TABLE langgraph.checkpoint_blobs (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        version TEXT NOT NULL,
        type TEXT NOT NULL,
        blob BYTEA,
        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    );
    CREATE TABLE langgraph.checkpoint_writes (
        thread_id TEXT NOT NULL,
        checkpoint_ns TEXT NOT NULL DEFAULT '',
        checkpoint_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        channel TEXT NOT NULL,
        type TEXT,
        blob BYTEA NOT NULL,
        task_path TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    );
    CREATE INDEX checkpoints_thread_id_idx ON langgraph.checkpoints (thread_id);
    CREATE INDEX checkpoint_blobs_thread_id_idx ON langgraph.checkpoint_blobs (thread_id);
    CREATE INDEX checkpoint_writes_thread_id_idx ON langgraph.checkpoint_writes (thread_id);
"""


def _kinds(kinds: Sequence[str]) -> str:
    return ", ".join(f"'{k}'" for k in kinds)


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
            CHECK (type IN ('ingest_dataset', 'analyze_dataset', 'run_investigation'));

        -- Tool evidence: unique per (revision, tool, params) so immutable results are reused.
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_kinds(DETECTION_KINDS + TOOL_KINDS)}));
        ALTER TABLE evidence_items
            ADD COLUMN params_hash text CHECK (params_hash ~ '^[0-9a-f]{{64}}$'),
            ADD COLUMN query_version text,
            ADD CONSTRAINT evidence_items_tool_fields CHECK (
                (kind IN ({_kinds(DETECTION_KINDS)}))
                OR (params_hash IS NOT NULL AND query_version IS NOT NULL));
        CREATE UNIQUE INDEX evidence_items_tool_key
            ON evidence_items (revision_id, kind, params_hash) WHERE params_hash IS NOT NULL;

        CREATE TABLE investigations (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            revision_id uuid NOT NULL REFERENCES analysis_revisions (id) ON DELETE CASCADE,
            status text NOT NULL CHECK (status IN ('queued', 'running', 'completed',
                'completed_no_incidents', 'insufficient_data', 'failed')),
            phase text,
            llm_mode text NOT NULL CHECK (llm_mode IN ('llm', 'deterministic')),
            llm_disabled_reason text,
            graph_version text NOT NULL,
            catalog_version text NOT NULL,
            last_seq integer NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
            error_code text,
            started_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz
        );
        CREATE INDEX investigations_business_idx ON investigations (business_id, created_at DESC);
        CREATE INDEX investigations_dataset_idx ON investigations (dataset_id);

        CREATE TABLE investigation_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            seq integer NOT NULL CHECK (seq >= 1),
            type text NOT NULL,
            actor_kind text NOT NULL CHECK (actor_kind IN ('system', 'llm', 'human')),
            payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            dedupe_key text NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT investigation_events_seq UNIQUE (investigation_id, seq),
            CONSTRAINT investigation_events_dedupe UNIQUE (investigation_id, dedupe_key)
        );

        CREATE TABLE llm_calls (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            task text NOT NULL,
            attempt smallint NOT NULL CHECK (attempt >= 1),
            provider text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{{64}}$'),
            input jsonb NOT NULL,
            output jsonb,
            stop_reason text,
            tokens_in integer NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
            tokens_out integer NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
            latency_ms integer NOT NULL CHECK (latency_ms >= 0),
            validation text NOT NULL
                CHECK (validation IN ('accepted', 'repaired', 'invalid', 'error')),
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX llm_calls_investigation_idx ON llm_calls (investigation_id);

        CREATE TABLE hypotheses (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            incident_id uuid NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
            template text NOT NULL,
            catalog_version text NOT NULL,
            priority smallint NOT NULL CHECK (priority BETWEEN 1 AND 99),
            source text NOT NULL CHECK (source IN ('catalog', 'llm_prioritized')),
            rationale jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT hypotheses_key UNIQUE (investigation_id, incident_id, template)
        );

        CREATE TABLE hypothesis_evaluations (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            hypothesis_id uuid NOT NULL REFERENCES hypotheses (id) ON DELETE CASCADE,
            status text NOT NULL CHECK (status IN
                ('supported', 'refuted', 'inconclusive', 'needs_human_context')),
            confidence numeric(5, 4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            data_confidence numeric(5, 4) CHECK (data_confidence BETWEEN 0 AND 1),
            human_confidence numeric(5, 4) CHECK (human_confidence BETWEEN 0 AND 1),
            criteria jsonb NOT NULL,
            evaluated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT hypothesis_evaluations_once UNIQUE (hypothesis_id)
        );

        CREATE TABLE investigation_evidence (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            evidence_id uuid NOT NULL REFERENCES evidence_items (id) ON DELETE CASCADE,
            incident_id uuid NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
            requested_by text NOT NULL CHECK (requested_by IN ('detection', 'system', 'llm')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (investigation_id, evidence_id)
        );

        CREATE TABLE clarifications (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            incident_id uuid NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
            incident_key text NOT NULL,
            hypothesis_id uuid REFERENCES hypotheses (id) ON DELETE CASCADE,
            criterion_key text NOT NULL,
            trigger text NOT NULL CHECK (trigger IN ('CT-1', 'CT-2', 'CT-3', 'CT-4')),
            question_spec jsonb NOT NULL,
            status text NOT NULL DEFAULT 'open' CHECK (status IN ('open')),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT clarifications_key UNIQUE (investigation_id, incident_key, criterion_key)
        );

        CREATE TABLE findings (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            investigation_id uuid NOT NULL REFERENCES investigations (id) ON DELETE CASCADE,
            round smallint NOT NULL CHECK (round >= 1),
            content jsonb NOT NULL,
            rendered jsonb NOT NULL,
            source text NOT NULL CHECK (source IN ('llm', 'deterministic')),
            llm_call_id uuid REFERENCES llm_calls (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT findings_round UNIQUE (investigation_id, round)
        );
    """)

    statements = []
    for table in ("investigations", *INSERT_ONLY):
        statements.append(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT TO {APP_ROLE}
                USING ({TENANT});
            CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT TO {APP_ROLE}
                WITH CHECK ({TENANT});
            GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
        """)
    statements.append(f"""
        CREATE POLICY investigations_tenant_update ON investigations FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (status, phase, llm_mode, llm_disabled_reason, last_seq, error_code,
            started_at, completed_at) ON investigations TO {APP_ROLE};

        -- A finished investigation is a record: only a failed one may be re-queued.
        CREATE FUNCTION investigations_terminal_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('completed', 'completed_no_incidents', 'insufficient_data') THEN
                RAISE EXCEPTION 'investigation % is % and immutable', OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER investigations_terminal_immutable BEFORE UPDATE ON investigations
            FOR EACH ROW EXECUTE FUNCTION investigations_terminal_immutable();
    """)
    op.execute("\n".join(statements))

    op.execute(CHECKPOINT_DDL)
    op.execute(f"""
        INSERT INTO langgraph.checkpoint_migrations (v)
            SELECT generate_series(0, {CHECKPOINT_MIGRATIONS - 1});
        GRANT USAGE ON SCHEMA langgraph TO {APP_ROLE};
        GRANT SELECT ON langgraph.checkpoint_migrations TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE ON langgraph.checkpoints, langgraph.checkpoint_blobs,
            langgraph.checkpoint_writes TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
        DROP SCHEMA langgraph CASCADE;
        DROP TABLE findings;
        DROP TABLE clarifications;
        DROP TABLE investigation_evidence;
        DROP TABLE hypothesis_evaluations;
        DROP TABLE hypotheses;
        DROP TABLE llm_calls;
        DROP TABLE investigation_events;
        DROP TABLE investigations;
        DROP FUNCTION investigations_terminal_immutable();
        DELETE FROM evidence_items WHERE params_hash IS NOT NULL;
        DROP INDEX evidence_items_tool_key;
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_tool_fields,
            DROP COLUMN query_version, DROP COLUMN params_hash;
        ALTER TABLE evidence_items DROP CONSTRAINT evidence_items_kind_check;
        ALTER TABLE evidence_items ADD CONSTRAINT evidence_items_kind_check
            CHECK (kind IN ({_kinds(DETECTION_KINDS)}));
        DELETE FROM jobs WHERE type = 'run_investigation';
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
            CHECK (type IN ('ingest_dataset', 'analyze_dataset'));
    """)
