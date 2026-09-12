from django.contrib import admin
from django.urls import path

from core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("kaydedilenler/", views.saved, name="saved"),
    path("gizlilik/", views.privacy, name="privacy"),
    path("api/suggest/", views.api_suggest, name="api_suggest"),
    path("api/state/", views.api_state, name="api_state"),
    path("healthz/", views.healthz, name="healthz"),
    path("yonetim/", admin.site.urls),
]
