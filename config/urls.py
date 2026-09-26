from django.urls import path

from ledger.api.app import api

urlpatterns = [
    path("", api.urls),
]
