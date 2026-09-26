from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

CURRENCIES = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "BTC": 8,
    "ETH": 18,
    "USDT": 6,
}


def seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    currency = apps.get_model("ledger", "Currency")
    for code, scale in CURRENCIES.items():
        currency.objects.update_or_create(code=code, defaults={"scale": scale})


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_append_only_ledger")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
