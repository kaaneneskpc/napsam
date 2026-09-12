"""
Yavas degisen yapilandirmanin surec ici onbellegi.

Neden var: sunucusuz ortamda her istek veritabanina ayri gidis-donus yapar.
Olculen degerler (Turkiye -> Frankfurt): bos bir sorgu bile ~80 ms. pick()
dort sorgu atiyordu ve bunlarin ucu -- haftalik tema, butce kademeleri,
asgari ucret -- gunde bir bile degismeyen verilerdi. Ucu de ~216 ms'e mal
oluyordu; bu, "acilistan oneriye < 3 sn" hedefinin dortte biri.

TTL bilincli olarak kisa: yonetim panelinden asgari ucret ya da bir kademe
degistirildiginde en gec TTL kadar sonra yururluge girer. Kod dagitimi
gerekmemesi kurali (Bolum 7.2) bozulmaz, sadece birkac dakika gecikir.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache

CONFIG_TTL = 300  # saniye

_TIERS_KEY = "napsam:budget_tiers"
_WAGE_KEY = "napsam:net_minimum_wage"
_THEME_KEY = "napsam:weekly_theme"


def budget_tiers() -> list:
    """Etkin butce kademeleri, sirali."""
    tiers = cache.get(_TIERS_KEY)
    if tiers is None:
        from core.models import BudgetTier

        tiers = list(BudgetTier.objects.filter(is_active=True).order_by("order"))
        cache.set(_TIERS_KEY, tiers, CONFIG_TTL)
    return tiers


def budget_tier(key: str):
    return next((t for t in budget_tiers() if t.key == key), None)


def current_wage() -> Decimal:
    """Net asgari ucret. Koda gomulu degil; RemoteConfig'ten gelir."""
    wage = cache.get(_WAGE_KEY)
    if wage is None:
        from core.models import RemoteConfig

        wage = Decimal(str(RemoteConfig.get("net_minimum_wage", 28075)))
        cache.set(_WAGE_KEY, wage, CONFIG_TTL)
    return wage


def active_theme():
    """
    Bolum 7.6 - haftalik tema. Kullanici gormez, siralamayi agirliklandirir.

    Onbellekte 'yok' ile 'hic tema yok' ayrimi icin sentinel kullanilir;
    aksi halde tema tanimli degilken her istek bosuna sorgu atardi.
    """
    cached = cache.get(_THEME_KEY)
    if cached is not None:
        return cached or None

    from django.utils import timezone

    from core.models import WeeklyTheme

    theme = (
        WeeklyTheme.objects.filter(is_active=True, starts_on__lte=timezone.localdate())
        .order_by("-starts_on")
        .first()
    )
    cache.set(_THEME_KEY, theme or False, CONFIG_TTL)
    return theme


def invalidate() -> None:
    """Yonetim panelinden degisiklik yapildiginda cagrilir."""
    cache.delete_many([_TIERS_KEY, _WAGE_KEY, _THEME_KEY])
