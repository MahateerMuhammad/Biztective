"""Analytics: cancelled lines, optional costs, analysis revisions, segments, incidents, evidence.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

import json
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
TENANT = "business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid"
INSERT_ONLY = ("customer_segments", "incidents", "evidence_items")

# DATA_CONTRACTS §3.5 v1 plus the AMD-10 segmentation constants. Immutable: changes create v2.
CONFIG_V1 = {
    "current_weeks": 4,
    "baseline_weeks": 12,
    "min_baseline_weeks": 8,
    "z_threshold": "3.0",
    "rel_change_threshold": "0.15",
    "rate_abs_threshold_pp": "0.02",
    "rate_rel_threshold": "0.20",
    "materiality_pct": "0.01",
    "min_impact_amount": "0.00",
    "min_slice_orders_baseline": 30,
    "min_slice_orders_current": 10,
    "min_units_current_refund_rate": 30,
    "product_slice_coverage": "0.80",
    "max_product_slices": 50,
    "child_share_threshold": "0.60",
    "overlap_jaccard_threshold": "0.60",
    "max_incidents": 5,
    "max_investigated_incidents": 3,
    "return_maturity_days": 14,
    "rfm_lookback_days": 365,
    "churn_gap_multiplier": "2.0",
    "churn_min_recency_days": 30,
    "dormant_days": 180,
    "dormant_gap_multiplier": "4.0",
    "min_history_days": 60,
    "return_risk_min_rate": "0.30",
    "return_risk_multiplier": "2.0",
    "key_account_share": "0.05",
    "low_sample_customers": 20,
    "severity_critical_share": "0.05",
    "severity_high_share": "0.025",
    "severity_medium_share": "0.01",
    "evidence_sample_size": 25,
}


def upgrade() -> None:
    op.execute("""
        ALTER TABLE order_lines ADD COLUMN status text NOT NULL DEFAULT 'completed'
            CHECK (status IN ('completed', 'cancelled'));
        CREATE INDEX order_lines_completed_date_idx ON order_lines (dataset_id, order_date)
            WHERE status = 'completed';
        ALTER TABLE products ALTER COLUMN unit_cost DROP NOT NULL;

        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
            CHECK (type IN ('ingest_dataset', 'analyze_dataset'));

        CREATE TABLE analysis_configs (
            version integer PRIMARY KEY CHECK (version >= 1),
            constants jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE analysis_revisions (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            config_version integer NOT NULL REFERENCES analysis_configs (version),
            code_version text NOT NULL,
            status text NOT NULL CHECK (status IN
                ('queued', 'running', 'completed', 'insufficient_data', 'failed')),
            as_of_date date,
            current_window daterange,
            baseline_window daterange,
            restrictions jsonb NOT NULL DEFAULT '[]'::jsonb,
            summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            metrics_hash text,
            segments_hash text,
            incidents_hash text,
            revision_hash text,
            error_code text,
            requested_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT analysis_revisions_key UNIQUE (dataset_id, config_version, code_version)
        );

        CREATE TABLE customer_segments (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            revision_id uuid NOT NULL REFERENCES analysis_revisions (id) ON DELETE CASCADE,
            customer_id text NOT NULL,
            segment text NOT NULL
                CHECK (segment IN ('loyal', 'at_risk', 'dormant', 'insufficient_history')),
            r_score smallint NOT NULL CHECK (r_score BETWEEN 1 AND 5),
            f_score smallint NOT NULL CHECK (f_score BETWEEN 1 AND 5),
            m_score smallint NOT NULL CHECK (m_score BETWEEN 1 AND 5),
            first_order_date date NOT NULL,
            last_order_date date NOT NULL,
            recency_days integer NOT NULL CHECK (recency_days >= 0),
            lifetime_orders integer NOT NULL CHECK (lifetime_orders >= 1),
            frequency_365 integer NOT NULL CHECK (frequency_365 >= 0),
            monetary_365 numeric(20, 4) NOT NULL,
            median_gap_days numeric(10, 1),
            flags text[] NOT NULL DEFAULT '{}',
            PRIMARY KEY (revision_id, customer_id)
        );
        CREATE INDEX customer_segments_segment_idx ON customer_segments (revision_id, segment);

        CREATE TABLE incidents (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            revision_id uuid NOT NULL REFERENCES analysis_revisions (id) ON DELETE CASCADE,
            key text NOT NULL,
            metric text NOT NULL,
            dimension text NOT NULL,
            dimension_value text,
            rank smallint NOT NULL CHECK (rank >= 1),
            status text NOT NULL CHECK (status IN ('investigated', 'not_investigated')),
            severity text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
            impact numeric(20, 4) NOT NULL,
            stats jsonb NOT NULL,
            affected jsonb NOT NULL,
            context jsonb NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT incidents_revision_key UNIQUE (revision_id, key)
        );

        CREATE TABLE evidence_items (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            revision_id uuid NOT NULL REFERENCES analysis_revisions (id) ON DELETE CASCADE,
            incident_id uuid NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
            kind text NOT NULL
                CHECK (kind IN ('incident_detection', 'source_records', 'return_reason_mix')),
            params jsonb NOT NULL,
            result jsonb NOT NULL,
            result_hash text NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX evidence_items_incident_idx ON evidence_items (incident_id);
    """)
    op.execute(
        "INSERT INTO analysis_configs (version, constants) VALUES (1, '"
        + json.dumps(CONFIG_V1, sort_keys=True)
        + "'::jsonb)"
    )

    statements = [f"GRANT SELECT ON analysis_configs TO {APP_ROLE};"]
    for table in (*INSERT_ONLY, "analysis_revisions"):
        statements.append(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT TO {APP_ROLE}
                USING ({TENANT});
            CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT TO {APP_ROLE}
                WITH CHECK ({TENANT});
            GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
        """)
    statements.append(f"""
        CREATE POLICY analysis_revisions_tenant_update ON analysis_revisions FOR UPDATE
            TO {APP_ROLE} USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (status, as_of_date, current_window, baseline_window, restrictions, summary,
            metrics_hash, segments_hash, incidents_hash, revision_hash, error_code, completed_at)
            ON analysis_revisions TO {APP_ROLE};

        -- Completed analysis results are immutable.
        CREATE FUNCTION analysis_revisions_terminal_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('completed', 'insufficient_data') THEN
                RAISE EXCEPTION 'analysis revision % is % and immutable', OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER analysis_revisions_terminal_immutable BEFORE UPDATE ON analysis_revisions
            FOR EACH ROW EXECUTE FUNCTION analysis_revisions_terminal_immutable();

        -- Fresh planner statistics after each ingestion (bulk inserts leave them stale and
        -- cause pathological join plans). Owner-defined: the app role may only EXECUTE it;
        -- it takes no input and touches statistics of four fixed tables.
        CREATE FUNCTION refresh_dataset_statistics() RETURNS void
            LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            ANALYZE products;
            ANALYZE customers;
            ANALYZE order_lines;
            ANALYZE returns;
        END $$;
        REVOKE ALL ON FUNCTION refresh_dataset_statistics() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION refresh_dataset_statistics() TO {APP_ROLE};
    """)
    op.execute("\n".join(statements))


def downgrade() -> None:
    op.execute(f"""
        DROP FUNCTION refresh_dataset_statistics();
        DROP TABLE evidence_items;
        DROP TABLE incidents;
        DROP TABLE customer_segments;
        DROP TABLE analysis_revisions;
        DROP FUNCTION analysis_revisions_terminal_immutable();
        REVOKE SELECT ON analysis_configs FROM {APP_ROLE};
        DROP TABLE analysis_configs;
        DELETE FROM jobs WHERE type = 'analyze_dataset';
        ALTER TABLE jobs DROP CONSTRAINT jobs_type_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_type_check CHECK (type IN ('ingest_dataset'));
        DROP INDEX order_lines_completed_date_idx;
        ALTER TABLE order_lines DROP COLUMN status;
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM products WHERE unit_cost IS NULL) THEN
                RAISE EXCEPTION 'cannot downgrade: products without unit_cost exist';
            END IF;
        END $$;
        ALTER TABLE products ALTER COLUMN unit_cost SET NOT NULL;
    """)
