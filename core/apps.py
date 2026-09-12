from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "NAPSAM"

    def ready(self):
        from core import signals  # noqa: F401  (sinyalleri kaydeder)
