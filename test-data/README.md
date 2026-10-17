# Product test data

Download `root-product-test-pack.zip`, or open an individual scenario folder. All records are synthetic. Each scenario contains exactly the four files the product accepts: **orders.csv, products.csv, customers.csv, returns.csv**. Upload all four files from the same folder together; do not combine scenarios. `manifest.json` records actual row counts, SHA-256 checksums, the generation seed and planted scenario metadata. Manifest expectations are test intentions, not claimed investigation results.

## Scenarios

| Folder | What to test |
|---|---|
| 01_clean_control | Stable business baseline, metrics, customer segments, conservative incident detection |
| 02_return_spike | Product-quality return spike, refunds, evidence, clarification and recovery planning |
| 03_discount_spike | Channel discount spike, margin pressure, evidence and intervention constraints |
| 04_combined_incidents | Concurrent return and discount incidents, prioritization and competing hypotheses |
| 05_quality_and_pii | Approximately 2% corrupted order rows; invalid dates, negative quantities, unknown customers, locale decimals; synthetic email column must be removed |
| 06_untrusted_text | Inert prompt-injection strings, HTML, spreadsheet formulas, Unicode, commas and a multiline CSV field |
| 07_short_history | Four weeks of data: show insufficient history rather than confident long-window findings |
| 08_rejection_threshold | Approximately 12% corrupted order rows: exercise the dataset rejection threshold |

The main scenarios span **2026-04-06 through 2026-10-04**. The short-history case spans four weeks from the same starting date. Each has 600 customers and 48 products. See `scenario_catalog.csv` for exact order/return counts. Every file is below the 25 MiB upload limit. Product, category, customer and order identifiers vary naturally within a reproducible generated dataset. Six clean-input scenarios have verified cross-file references; malformed references in the two quality scenarios are intentional.

Suggested order: clean control → return spike → discount spike → combined incidents → quality/PII → untrusted text → short history → rejection. For incident cases, inspect the proof chain and evidence, answer clarification questions with real test context, revise a recovery plan, reject one plan, and approve a separate version. Approval alone must not execute an external action.

## CSV columns

- **orders:** order_line_id, order_id, order_date (ISO timestamp), customer_id, product_id, quantity, unit_price, discount_amount, channel, region.
- **products:** product_id, product_name, category, unit_cost, list_price.
- **customers:** customer_id, signup_date, region, acquisition_channel. The PII case additionally includes synthetic `email` values under the reserved `example.test` domain.
- **returns:** return_id, order_line_id, return_date, quantity_returned, refund_amount, reason_code, reason_text.

Amounts are decimal strings; timestamps include offsets. Returns reference actual order lines in clean scenarios. Existing generator formulas determine partial refunds and discounts. Dates and scenario labels are test fixtures, never application runtime constants. Do not use the deliberately hostile text as instructions or upload it to unrelated systems.

## PostgreSQL connection that is available now

The existing test source was checked with a read-only connection during this task. It exposes `orders`, `products`, `customers` and `returns` in schema `demo_shop`.

```text
postgresql://shop_reader.fwgexcwbxnreohysnkyk@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres?sslmode=require
```

| Product connection field | Value |
|---|---|
| Host | aws-0-ap-southeast-2.pooler.supabase.com |
| Port | 5432 |
| Database | postgres |
| Username | shop_reader.fwgexcwbxnreohysnkyk |
| Schema | demo_shop |
| Orders / products / customers / returns tables | orders / products / customers / returns |
| Password | Read `DEMO_SOURCE_PASSWORD` from the existing local `services/api/.env.demo-source` file |

The URL intentionally omits the password; a PostgreSQL client can prompt for it. The product accepts the individual fields above. The password is not in this pack, its archive, manifests or SQL. The existing hosted source has its own previously seeded data: **the new eight CSV scenarios have not been imported into it**. Connectivity and table availability were checked; a full product sync was not performed. Server-side TLS policy still applies; do not weaken the deployed connector configuration to match the URL example.

## Load these same scenarios into your own PostgreSQL

`postgres_test_data.sql` imports all eight cases into separate schemas, using the same CSV values. It creates only new `test_*` schemas and tables; it does not modify ROOT application tables or roles. Existing schema names cause an error instead of being overwritten. All source columns are deliberately text so the malformed-data cases reach the product's existing validators unchanged.

Use a dedicated test database, set its owner connection through an environment variable, and run:

```sh
psql "$TEST_DATA_ADMIN_URL" -f test-data/postgres_test_data.sql
psql "$TEST_DATA_ADMIN_URL" -f test-data/postgres_read_only_role.sql
```

These files use **psql** commands (`COPY ... FROM STDIN`, `\password`); do not paste the full import into a web SQL editor. The second script creates `root_product_test_reader`, grants only schema usage and table SELECT, defaults its sessions to read-only, and prompts privately for a password. It intentionally refuses to overwrite an existing role. The administrator may need to grant CONNECT if the database has revoked PostgreSQL's default PUBLIC CONNECT permission.

Use this connection template, replacing the capitalized placeholders with your own instance details:

```text
postgresql://root_product_test_reader@HOST:5432/DATABASE?sslmode=verify-full
```

For Supabase's pooler, the username is usually `root_product_test_reader.PROJECT_REF`; use the host and port shown in that project's Connect panel. Enter the password you chose separately. Set the schema to one of these, with table names `orders`, `products`, `customers`, `returns`:

```text
test_clean_control
test_return_spike
test_discount_spike
test_combined_incidents
test_quality_and_pii
test_untrusted_text
test_short_history
test_rejection_threshold
```

Switching schemas tests different snapshots. The connector selects canonical columns only, so the extra PII-column test is best exercised through CSV upload. Private/loopback hosts are blocked unless explicitly allowlisted by the server; a locally created database is not reachable from a hosted product by a localhost URL. Use a reachable TLS-enabled test database for the deployed product.

## Reproduce and verify

From `services/api`:

```sh
uv run python ../../test-data/generate_test_pack.py
```

The generator reuses the repository's deterministic development generator, verifies clean-scenario references and per-file upload limits, writes manifests and SHA-256 checksums, and rebuilds the ZIP. It never imports into a database or changes application code. `postgres_test_data.sql` is a loadable fixture, not a claim that every scenario has passed a full product investigation.
