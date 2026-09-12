"""
Oneri motoru - Katman 1 (seed havuzu).

Bolum 10: trafigin ~%85'i buradan karsilanir. Sifir maliyet, sifir
halusinasyon, internetsiz calisabilir.

Degerlendirme sirasi Bolum 11'deki sirayla aynidir:
    butce -> saat -> hava -> sure -> kiminle -> enerji -> ev/disari
    -> mevsim/takvim -> kelimeler

Ilk yedi adim SERT FILTREdir (uymayan oneri havuzdan duser).
Son iki adim ve tema YUMUSAK PUANdir (siralamayi etkiler, elemez).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from core.models import (
    BudgetTier,
    Energy,
    Place,
    RemoteConfig,
    StateStatus,
    Suggestion,
    SuggestionState,
    WeatherNeed,
    WeeklyTheme,
)

# Gece penceresi. Bolum 11 kural 8: gec saatte tek basina tenha yer onerme.
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6

# Mekanlarin buyuk cogunlugu bu saatler disinda kapalidir; kapali bir yere
# yollamak oneriyi bastan basarisiz kilar.
VENUE_OPEN_FROM = 8
VENUE_OPEN_UNTIL = 23

# Puanlama agirliklari. Tek yerde toplandi ki ayarlamasi kolay olsun.
W_KEYWORD = 3.0
W_THEME = 2.0
W_ENERGY = 2.0
W_COMPANION = 2.0
W_PLACE_PREF = 2.5
W_FREE_BONUS = 0.5
W_RECENT_CATEGORY_PENALTY = -2.5
W_ALREADY_SHOWN_PENALTY = -1.5
W_JITTER = 1.2


@dataclass(slots=True)
class Context:
    """Bir oneri istegi icin tum baglam. Sehir/ilce alani YOKTUR."""

    budget_key: str = "bedava"
    keywords: list[str] = field(default_factory=list)
    duration_max: int | None = None
    companions: str | None = None
    energy: str | None = None
    place_pref: str | None = None  # "ev" | "disari" | None
    # Hava yalnizca istemci gonderirse bilinir. Konum izni istemiyoruz
    # (Sadelik Anayasasi kural 3), bu yuzden cogu zaman None'dir.
    weather: dict | None = None
    now: datetime = field(default_factory=timezone.localtime)
    excluded: set[str] = field(default_factory=set)

    @property
    def is_night(self) -> bool:
        h = self.now.hour
        return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR

    @property
    def is_solo(self) -> bool:
        return self.companions in (None, "", "solo")


# --------------------------------------------------------------------------
# Butce
# --------------------------------------------------------------------------
def current_wage() -> Decimal:
    """Net asgari ucret. Koda gomulu degil; RemoteConfig'ten okunur."""
    return Decimal(str(RemoteConfig.get("net_minimum_wage", 28075)))


def budget_ceiling(budget_key: str) -> int | None:
    """
    Kullanicinin sectigi kademenin ust siniri (TL). None = sinir yok.

    Bolum 8.2 ekonomik gerceklik kurali: kullanici butce belirtmediyse
    varsayilan 'bedava'dir ve ucretli oneri HIC gosterilmez.
    """
    tier = BudgetTier.objects.filter(key=budget_key, is_active=True).first()
    if tier is None:
        return 0  # taninmayan kademe -> en guvenli taraf: bedava
    return tier.amount_max(current_wage())


# --------------------------------------------------------------------------
# Sert filtreler
# --------------------------------------------------------------------------
def candidates(ctx: Context, profile=None) -> list[Suggestion]:
    """
    Baglama uyan tum onerileri dondurur.

    Alan bazli filtreler veritabaninda, JSON alanlarina (companions,
    seasonality) dayanan filtreler Python'da calisir. Sebep: JSONField
    `contains` aramasi SQLite'ta desteklenmiyor; havuz birkac yuz satir
    oldugu icin bellekte suzmek hem tasinabilir hem de bedelsiz.
    """
    # AI ile uretilenler genel havuza girmez; onlar yalnizca kendi
    # istegine cevap olarak dondurulur.
    qs = Suggestion.objects.active().seeds()

    # 1) BUTCE - asla asilmaz.
    ceiling = budget_ceiling(ctx.budget_key)
    if ceiling is not None:
        qs = qs.filter(cost_max__lte=ceiling)

    # 2) SAAT
    if ctx.is_night:
        # Gece tek basina riskli onerileri dusur.
        if ctx.is_solo:
            qs = qs.exclude(night_unsafe_solo=True)
        # Kapali olmasi kuvvetle muhtemel mekanlari dusur.
        if not (VENUE_OPEN_FROM <= ctx.now.hour < VENUE_OPEN_UNTIL):
            qs = qs.exclude(place=Place.MEKANLI)

    # 3) HAVA - yalnizca istemci gonderdiyse bilinir.
    if ctx.weather:
        condition = str(ctx.weather.get("condition", "")).lower()
        temp = ctx.weather.get("tempC")
        if condition in {"rain", "snow", "storm", "sleet"}:
            qs = qs.exclude(weather_need__in=[WeatherNeed.DRY, WeatherNeed.WARM])
        if isinstance(temp, (int, float)) and temp < 8:
            qs = qs.exclude(weather_need=WeatherNeed.WARM)

    # 4) SURE
    if ctx.duration_max:
        qs = qs.filter(duration_min__lte=ctx.duration_max)

    # 6) ENERJI - dusuk enerji istendiginde yuksek eforu ele.
    if ctx.energy == Energy.LOW:
        qs = qs.exclude(energy=Energy.HIGH)
    elif ctx.energy == Energy.HIGH:
        qs = qs.exclude(energy=Energy.LOW)

    # 7) EV / DISARI
    if ctx.place_pref == "ev":
        qs = qs.filter(place=Place.EV)
    elif ctx.place_pref == "disari":
        qs = qs.exclude(place=Place.EV)

    # 9) Kullaniciya ozel elemeler
    if ctx.excluded:
        qs = qs.exclude(slug__in=ctx.excluded)

    if profile is not None:
        cooling = SuggestionState.objects.filter(
            profile=profile,
            status=StateStatus.DISMISSED,
            dismissed_until__gt=ctx.now,
        ).values_list("suggestion_id", flat=True)
        qs = qs.exclude(id__in=list(cooling))

    month = ctx.now.month
    pool = []
    for item in qs:
        # 5) KIMINLE
        if ctx.companions and ctx.companions not in (item.companions or []):
            continue
        # 8) MEVSIM - bos liste = tum yil gecerli.
        season = item.seasonality or []
        if season and month not in season:
            continue
        pool.append(item)
    return pool


# --------------------------------------------------------------------------
# Yumusak puanlama
# --------------------------------------------------------------------------
def active_theme() -> WeeklyTheme | None:
    """Bolum 7.6: kullanicinin hic gormedigi haftalik tema."""
    today = timezone.localdate()
    return (
        WeeklyTheme.objects.filter(is_active=True, starts_on__lte=today)
        .order_by("-starts_on")
        .first()
    )


def score(
    suggestion: Suggestion,
    ctx: Context,
    theme: WeeklyTheme | None,
    recent_categories: set[str],
    shown_ids: set[int],
) -> float:
    value = 0.0

    # Kelimeler - kullanicinin tek acik sinyali, en agir basan sey.
    if ctx.keywords:
        tags = {str(t).lower() for t in (suggestion.tags or [])}
        matched = sum(1 for kw in ctx.keywords if kw.lower() in tags)
        value += W_KEYWORD * matched

    # Haftalik tema
    if theme and theme.key in (suggestion.theme_tags or []):
        value += W_THEME

    if ctx.energy and suggestion.energy == ctx.energy:
        value += W_ENERGY

    if ctx.companions and ctx.companions in (suggestion.companions or []):
        value += W_COMPANION

    if ctx.place_pref == "ev" and suggestion.place == Place.EV:
        value += W_PLACE_PREF
    elif ctx.place_pref == "disari" and suggestion.place != Place.EV:
        value += W_PLACE_PREF

    # Bedava oneriler, butce yuksek olsa bile hafif one cikar (Bolum 8.2).
    if suggestion.cost_max == 0:
        value += W_FREE_BONUS

    # Cesitlilik: son gorulen kategoriden uzaklas.
    if suggestion.category in recent_categories:
        value += W_RECENT_CATEGORY_PENALTY

    if suggestion.id in shown_ids:
        value += W_ALREADY_SHOWN_PENALTY

    # Kucuk rastgelelik: ayni baglamda hep ayni kart cikmasin.
    value += random.uniform(0, W_JITTER)
    return value


# --------------------------------------------------------------------------
# Secim
# --------------------------------------------------------------------------
def _relax(ctx: Context) -> Context | None:
    """
    Filtreler bos sonuc dondurduginde kisitlari sirayla gevsetir.

    Kullaniciya "sonuc bulunamadi" GOSTERILMEZ - bu uründe bos ekran,
    kullanicinin Instagram'a donmesi demektir. Butce hicbir zaman
    gevsetilmez; para yoksa yoktur.
    """
    if ctx.excluded:
        return replace(ctx, excluded=set())
    if ctx.keywords:
        return replace(ctx, keywords=[])
    if ctx.duration_max:
        return replace(ctx, duration_max=None)
    if ctx.companions:
        return replace(ctx, companions=None)
    if ctx.energy:
        return replace(ctx, energy=None)
    if ctx.place_pref:
        return replace(ctx, place_pref=None)
    if ctx.weather:
        return replace(ctx, weather=None)
    return None


def pick(ctx: Context, profile=None, *, top_n: int = 5) -> Suggestion | None:
    """Baglama en uygun TEK oneriyi dondurur (Sadelik Anayasasi kural 4)."""
    theme = active_theme()

    recent_categories: set[str] = set()
    shown_ids: set[int] = set()
    if profile is not None:
        recent = (
            SuggestionState.objects.filter(profile=profile)
            .order_by("-updated_at")
            .select_related("suggestion")[:5]
        )
        recent_categories = {s.suggestion.category for s in recent}
        shown_ids = {s.suggestion_id for s in recent}

    current = ctx
    while current is not None:
        pool = candidates(current, profile)
        if pool:
            ranked = sorted(
                pool,
                key=lambda s: score(s, current, theme, recent_categories, shown_ids),
                reverse=True,
            )
            return random.choice(ranked[:top_n])
        current = _relax(current)

    # Son care: havuzdaki herhangi bir bedava oneri. Bos ekran gosterme.
    return Suggestion.objects.active().seeds().filter(cost_max=0).order_by("?").first()


def mark_shown(profile, suggestion: Suggestion) -> None:
    state, _ = SuggestionState.objects.get_or_create(
        profile=profile, suggestion=suggestion
    )
    state.shown_count += 1
    if state.status == StateStatus.SHOWN:
        state.save(update_fields=["shown_count", "updated_at"])
    else:
        state.save()


def mark_dismissed(profile, suggestion: Suggestion) -> None:
    """Gecilen oneri 30 gun boyunca tekrar gosterilmez (Bolum 9)."""
    cooldown = timedelta(days=settings.NAPSAM_DISMISS_COOLDOWN_DAYS)
    SuggestionState.objects.update_or_create(
        profile=profile,
        suggestion=suggestion,
        defaults={
            "status": StateStatus.DISMISSED,
            "dismissed_until": timezone.now() + cooldown,
        },
    )
