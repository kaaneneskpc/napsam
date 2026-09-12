"""
Yonetim arayuzu.

Bu panel son kullaniciya ait DEGIL; editoryal icerik ekibi icindir.
Uygulamanin kendisinde istatistik paneli, tema magazasi vb. yoktur
(Sadelik Anayasasi, Bolum 3).
"""

from django.contrib import admin

from core.models import (
    AnonProfile,
    BudgetTier,
    RemoteConfig,
    Suggestion,
    SuggestionState,
    WeeklyTheme,
)


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = (
        "title", "category", "place", "cost_max", "duration_badge", "energy", "is_active",
    )
    list_filter = ("is_active", "source", "category", "place", "energy", "weather_need")
    search_fields = ("title", "hook", "slug", "tags")
    prepopulated_fields = {"slug": ("title",)}
    fieldsets = (
        ("Kart yüzü", {"fields": ("title", "hook", "steps", "fallback")}),
        ("Sınıflandırma", {"fields": ("slug", "source", "category", "tags", "theme_tags")}),
        ("Maliyet ve süre", {"fields": (("cost_min", "cost_max"), "cost_note",
                                        ("duration_min", "duration_max"))}),
        ("Bağlam", {"fields": ("place", "venue_type", "energy", "companions",
                               "weather_need", "seasonality", "cultural_tags",
                               "required_items", "night_unsafe_solo")}),
        ("Durum", {"fields": ("is_active",)}),
    )


@admin.register(BudgetTier)
class BudgetTierAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "ratio_min", "ratio_max", "order", "is_active")
    ordering = ("order",)


@admin.register(RemoteConfig)
class RemoteConfigAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "note", "updated_at")


@admin.register(WeeklyTheme)
class WeeklyThemeAdmin(admin.ModelAdmin):
    list_display = ("starts_on", "label", "key", "is_active")


@admin.register(AnonProfile)
class AnonProfileAdmin(admin.ModelAdmin):
    list_display = ("anon_id", "created_at", "last_seen_at")
    readonly_fields = ("anon_id", "created_at", "last_seen_at")


@admin.register(SuggestionState)
class SuggestionStateAdmin(admin.ModelAdmin):
    list_display = ("profile", "suggestion", "status", "updated_at")
    list_filter = ("status",)
