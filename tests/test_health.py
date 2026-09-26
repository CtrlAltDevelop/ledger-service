import pytest
from django.test import Client


@pytest.mark.django_db
def test_healthz_reports_ok() -> None:
    response = Client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
