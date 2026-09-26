from io import StringIO

import pytest
from django.core.management import call_command

from ledger.models import ApiClient, BalanceSnapshot
from ledger.services import clients
from tests.conftest import AccountFactory, fund


@pytest.mark.django_db
def test_create_api_client_prints_a_working_token() -> None:
    out, err = StringIO(), StringIO()

    call_command("create_api_client", "backoffice", stdout=out, stderr=err)

    token = out.getvalue().strip()
    assert token.startswith("lsk_")
    assert clients.authenticate(token) == ApiClient.objects.get(name="backoffice")
    assert "store this token now" in err.getvalue()


def test_snapshot_balances_skips_quiet_accounts(make_account: AccountFactory) -> None:
    busy, quiet, idle = make_account(), make_account(), make_account()
    fund(busy, "1")
    fund(busy, "2")
    fund(quiet, "1")
    out = StringIO()

    call_command("snapshot_balances", "--min-entries", "2", stdout=out)

    # busy and the settlement account (three entries) qualify; quiet and idle do not.
    assert "took 2 snapshot(s)" in out.getvalue()
    assert BalanceSnapshot.objects.filter(account=busy).exists()
    assert not BalanceSnapshot.objects.filter(account__in=[quiet, idle]).exists()
