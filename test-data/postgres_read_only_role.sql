-- Run only against your dedicated test database, as its owner, using psql.
-- No embedded password. Existing role name causes an error rather than being changed.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE root_product_test_reader LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA "test_clean_control", "test_return_spike", "test_discount_spike", "test_combined_incidents", "test_quality_and_pii", "test_untrusted_text", "test_short_history", "test_rejection_threshold" TO root_product_test_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA "test_clean_control", "test_return_spike", "test_discount_spike", "test_combined_incidents", "test_quality_and_pii", "test_untrusted_text", "test_short_history", "test_rejection_threshold" TO root_product_test_reader;
ALTER ROLE root_product_test_reader SET default_transaction_read_only = on;
COMMIT;
\password root_product_test_reader
