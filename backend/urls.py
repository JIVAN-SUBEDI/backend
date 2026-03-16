from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/accounts/", include("accounts.urls")),
    path("api/", include("casinos.urls")),
    path("api/", include("customer.urls")),
    path("api/", include("analytics.urls")),
]