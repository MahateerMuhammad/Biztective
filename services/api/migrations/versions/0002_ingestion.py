"""Dataset ingestion: files, canonical records, validation issues, job queue, idempotency keys.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "root_app"
CURRENT_USER_ID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
CURRENT_BUSINESS_ID = "NULLIF(current_setting('app.current_business_id', true), '')::uuid"
TENANT = f"business_id = {CURRENT_BUSINESS_ID}"

RECORD_TABLES = ("products", "customers", "order_lines", "returns")
INSERT_ONLY_TENANT_TABLES = (
    *RECORD_TABLES,
    "validation_issues",
    "dataset_files",
    "idempotency_keys",
)


def upgrade() -> None:
    # --- datasets: lifecycle columns; the 0001 table is empty (no write path existed) ---
    op.execute("""
        ALTER TABLE datasets DROP CONSTRAINT datasets_status_check;
        ALTER TABLE datasets ADD CONSTRAINT datasets_status_check CHECK (status IN
            ('awaiting_mapping', 'queued', 'validating', 'ready', 'rejected', 'failed'));
        ALTER TABLE datasets
            ADD COLUMN source_timezone text NOT NULL DEFAULT 'UTC'
                CHECK (length(source_timezone) BETWEEN 1 AND 64),
            ADD COLUMN business_timezone text NOT NULL DEFAULT 'UTC'
                CHECK (length(business_timezone) BETWEEN 1 AND 64),
            ADD COLUMN currency text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
            ADD COLUMN issue_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN error_code text,
            ADD COLUMN completed_at timestamptz;
        ALTER TABLE datasets ALTER COLUMN source_timezone DROP DEFAULT;
        ALTER TABLE datasets ALTER COLUMN business_timezone DROP DEFAULT;
        ALTER TABLE datasets ALTER COLUMN currency DROP DEFAULT;
        ALTER TABLE datasets RENAME COLUMN sales_rows TO orders_rows;
        CREATE INDEX datasets_business_checksum_idx ON datasets (business_id, checksum_sha256);

        -- Terminal datasets are immutable (FR-03).
        CREATE FUNCTION datasets_terminal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('ready', 'rejected', 'failed') THEN
                RAISE EXCEPTION 'dataset % is % and immutable', OLD.id, OLD.status;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER datasets_terminal_immutable BEFORE UPDATE ON datasets
            FOR EACH ROW EXECUTE FUNCTION datasets_terminal_immutable();
    """)

    op.execute("""
        CREATE TABLE dataset_files (
            id uuid PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            kind text NOT NULL CHECK (kind IN ('orders', 'products', 'customers', 'returns')),
            original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
            size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            raw bytea,
            headers jsonb NOT NULL,
            mapping jsonb NOT NULL,
            mapping_confirmed_by uuid REFERENCES users (id),
            mapping_confirmed_at timestamptz,
            rows_total integer CHECK (rows_total >= 0),
            rows_accepted integer CHECK (rows_accepted >= 0),
            rows_rejected integer CHECK (rows_rejected >= 0),
            rows_duplicate integer CHECK (rows_duplicate >= 0),
            profile jsonb,
            purged_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT dataset_files_kind_key UNIQUE (dataset_id, kind)
        );

        CREATE TABLE products (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            product_id text NOT NULL,
            product_name text NOT NULL,
            category text NOT NULL,
            unit_cost numeric(18, 4) NOT NULL CHECK (unit_cost >= 0),
            list_price numeric(18, 4) NOT NULL CHECK (list_price > 0),
            source_row integer NOT NULL CHECK (source_row >= 2),
            PRIMARY KEY (dataset_id, product_id)
        );

        CREATE TABLE customers (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            customer_id text NOT NULL,
            signup_date date,
            region text,
            acquisition_channel text,
            source_row integer NOT NULL CHECK (source_row >= 2),
            PRIMARY KEY (dataset_id, customer_id)
        );

        CREATE TABLE order_lines (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            order_line_id text NOT NULL,
            order_id text NOT NULL,
            order_date date NOT NULL,
            ordered_at timestamptz,
            customer_id text NOT NULL,
            product_id text NOT NULL,
            quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 100000),
            unit_price numeric(18, 4) NOT NULL CHECK (unit_price >= 0),
            discount_amount numeric(18, 4) NOT NULL CHECK (discount_amount >= 0),
            channel text NOT NULL,
            region text NOT NULL,
            source_row integer NOT NULL CHECK (source_row >= 2),
            PRIMARY KEY (dataset_id, order_line_id),
            FOREIGN KEY (dataset_id, customer_id) REFERENCES customers (dataset_id, customer_id),
            FOREIGN KEY (dataset_id, product_id) REFERENCES products (dataset_id, product_id),
            CONSTRAINT order_lines_discount_le_gross
                CHECK (discount_amount <= quantity * unit_price)
        );
        CREATE INDEX order_lines_date_idx ON order_lines (business_id, dataset_id, order_date);
        CREATE INDEX order_lines_customer_idx ON order_lines (dataset_id, customer_id);
        CREATE INDEX order_lines_product_idx ON order_lines (dataset_id, product_id);

        CREATE TABLE returns (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            return_id text NOT NULL,
            order_line_id text NOT NULL,
            return_date date NOT NULL,
            returned_at timestamptz,
            quantity_returned integer NOT NULL CHECK (quantity_returned >= 1),
            refund_amount numeric(18, 4) NOT NULL CHECK (refund_amount >= 0),
            reason_code text NOT NULL CHECK (reason_code IN ('damaged', 'defective', 'wrong_item',
                'not_as_described', 'late_delivery', 'changed_mind', 'other')),
            reason_text text,
            source_row integer NOT NULL CHECK (source_row >= 2),
            PRIMARY KEY (dataset_id, return_id),
            FOREIGN KEY (dataset_id, order_line_id)
                REFERENCES order_lines (dataset_id, order_line_id)
        );
        CREATE INDEX returns_date_idx ON returns (business_id, dataset_id, return_date);
        CREATE INDEX returns_line_idx ON returns (dataset_id, order_line_id);

        CREATE TABLE validation_issues (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
            file_kind text NOT NULL
                CHECK (file_kind IN ('orders', 'products', 'customers', 'returns', 'dataset')),
            row_number integer,
            column_name text,
            code text NOT NULL CHECK (code ~ '^V-[0-9]{3}$'),
            severity text NOT NULL CHECK (severity IN ('error', 'warning')),
            message text NOT NULL CHECK (length(message) BETWEEN 1 AND 300),
            excerpt text CHECK (length(excerpt) <= 100)
        );
        CREATE INDEX validation_issues_dataset_idx ON validation_issues (dataset_id, id);

        CREATE TABLE idempotency_keys (
            business_id uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            key text NOT NULL CHECK (length(key) BETWEEN 8 AND 100),
            request_hash text NOT NULL,
            response_status integer NOT NULL,
            response_body jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (business_id, user_id, key)
        );

        -- System table: leased by the worker across tenants, never exposed through the API.
        CREATE TABLE jobs (
            id uuid PRIMARY KEY,
            business_id uuid REFERENCES businesses (id) ON DELETE CASCADE,
            type text NOT NULL CHECK (type IN ('ingest_dataset')),
            payload jsonb NOT NULL,
            status text NOT NULL
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            priority smallint NOT NULL DEFAULT 100,
            attempts smallint NOT NULL DEFAULT 0,
            max_attempts smallint NOT NULL CHECK (max_attempts >= 1),
            run_after timestamptz NOT NULL DEFAULT now(),
            lease_expires_at timestamptz,
            dedupe_key text NOT NULL UNIQUE,
            last_error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        );
        CREATE INDEX jobs_queued_idx ON jobs (priority, run_after) WHERE status = 'queued';
        CREATE INDEX jobs_running_idx ON jobs (lease_expires_at) WHERE status = 'running';
    """)

    statements = []
    for table in INSERT_ONLY_TENANT_TABLES:
        statements.append(f"""
            ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_select ON {table} FOR SELECT TO {APP_ROLE}
                USING ({TENANT});
            CREATE POLICY {table}_tenant_insert ON {table} FOR INSERT TO {APP_ROLE}
                WITH CHECK ({TENANT});
            GRANT SELECT, INSERT ON {table} TO {APP_ROLE};
        """)
    op.execute("\n".join(statements))

    op.execute(f"""
        CREATE POLICY dataset_files_tenant_update ON dataset_files FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (mapping, mapping_confirmed_by, mapping_confirmed_at, rows_total,
            rows_accepted, rows_rejected, rows_duplicate, profile) ON dataset_files TO {APP_ROLE};

        CREATE POLICY datasets_tenant_insert ON datasets FOR INSERT TO {APP_ROLE}
            WITH CHECK ({TENANT});
        CREATE POLICY datasets_tenant_update ON datasets FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT INSERT ON datasets TO {APP_ROLE};
        GRANT UPDATE (status, products_rows, customers_rows, orders_rows, returns_rows, date_min,
            date_max, issue_counts, error_code, completed_at) ON datasets TO {APP_ROLE};

        CREATE POLICY businesses_owner_update ON businesses FOR UPDATE TO {APP_ROLE}
            USING (EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.business_id = businesses.id AND m.user_id = {CURRENT_USER_ID}
                  AND m.role = 'owner'
            ));
        GRANT UPDATE (currency, timezone) ON businesses TO {APP_ROLE};

        CREATE POLICY idempotency_keys_tenant_update ON idempotency_keys FOR UPDATE TO {APP_ROLE}
            USING ({TENANT}) WITH CHECK ({TENANT});
        GRANT UPDATE (request_hash, response_status, response_body, created_at, expires_at)
            ON idempotency_keys TO {APP_ROLE};

        GRANT SELECT, INSERT ON jobs TO {APP_ROLE};
        GRANT UPDATE (status, attempts, run_after, lease_expires_at, last_error_code, updated_at,
            finished_at) ON jobs TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
        REVOKE UPDATE (currency, timezone) ON businesses FROM {APP_ROLE};
        DROP POLICY businesses_owner_update ON businesses;
        DROP TABLE jobs;
        DROP TABLE idempotency_keys;
        DROP TABLE validation_issues;
        DROP TABLE returns;
        DROP TABLE order_lines;
        DROP TABLE customers;
        DROP TABLE products;
        DROP TABLE dataset_files;

        REVOKE INSERT ON datasets FROM {APP_ROLE};
        REVOKE UPDATE (status, products_rows, customers_rows, orders_rows, returns_rows, date_min,
            date_max, issue_counts, error_code, completed_at) ON datasets FROM {APP_ROLE};
        DROP POLICY datasets_tenant_insert ON datasets;
        DROP POLICY datasets_tenant_update ON datasets;
        DROP TRIGGER datasets_terminal_immutable ON datasets;
        DROP FUNCTION datasets_terminal_immutable();
        DROP INDEX datasets_business_checksum_idx;
        DELETE FROM datasets;
        ALTER TABLE datasets RENAME COLUMN orders_rows TO sales_rows;
        ALTER TABLE datasets
            DROP COLUMN source_timezone, DROP COLUMN business_timezone, DROP COLUMN currency,
            DROP COLUMN issue_counts,
            DROP COLUMN error_code, DROP COLUMN completed_at;
        ALTER TABLE datasets DROP CONSTRAINT datasets_status_check;
        ALTER TABLE datasets ADD CONSTRAINT datasets_status_check CHECK (status IN
            ('uploaded', 'validating', 'ready', 'rejected', 'deleted'));
    """)
