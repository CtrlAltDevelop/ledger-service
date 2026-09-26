"""Make the ledger append-only and balanced at the database level.

The service layer already refuses to edit history or post an unbalanced
transaction. These triggers make the same promises hold for anyone with a
database connection: a script, a migration, a tired operator in psql.
"""

from django.db import migrations

FORBID_MUTATION = """
CREATE FUNCTION ledger_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: the ledger is append-only', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation',
              HINT = 'Post a reversal transaction instead.';
END;
$$;

CREATE TRIGGER ledger_entry_append_only
    BEFORE UPDATE OR DELETE ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_forbid_mutation();

CREATE TRIGGER ledger_transaction_append_only
    BEFORE UPDATE OR DELETE ON ledger_transaction
    FOR EACH ROW EXECUTE FUNCTION ledger_forbid_mutation();
"""

# Deferred to COMMIT so a transaction's legs can be inserted one by one; the
# check runs once every leg is in.
REQUIRE_BALANCE = """
CREATE FUNCTION ledger_require_balanced_transaction() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    unbalanced_currency text;
BEGIN
    SELECT currency INTO unbalanced_currency
      FROM ledger_entry
     WHERE transaction_id = NEW.transaction_id
     GROUP BY currency
    HAVING SUM(amount) <> 0
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'transaction % does not balance in %',
            NEW.transaction_id, unbalanced_currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ledger_entry_balanced
    AFTER INSERT ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_require_balanced_transaction();
"""

DROP_ALL = """
DROP TRIGGER IF EXISTS ledger_entry_balanced ON ledger_entry;
DROP FUNCTION IF EXISTS ledger_require_balanced_transaction();
DROP TRIGGER IF EXISTS ledger_transaction_append_only ON ledger_transaction;
DROP TRIGGER IF EXISTS ledger_entry_append_only ON ledger_entry;
DROP FUNCTION IF EXISTS ledger_forbid_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [
        migrations.RunSQL(FORBID_MUTATION + REQUIRE_BALANCE, reverse_sql=DROP_ALL),
    ]
