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

from core import config
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
# DIKKAT: W_THEME, NEAR_SCORE'dan KUCUK kalmali. Buyuk oldugunda haftalik
# tema bir agirlik olmaktan cikip sert filtre gibi davraniyor: 58 adaylik
# bir havuzda secimlerin tamami temali 6 oneriye sikismisti. Bolum 7.6
# temayi "siralamayi agirliklandirir" diye tanimlar, daraltir demez.
W_THEME = 1.0
# Butce kademesi secildiginde o parayi GERCEKTEN kullanan oneriler one cikar.
# Aksi halde "Bol" secen kullaniciya bedava oneri donuyordu (olcum: %82).
W_IN_BAND = 3.5
W_BELOW_BAND = 1.0
W_FREE_WHEN_PAID = -2.0
W_ENERGY = 2.0
W_COMPANION = 2.0
W_PLACE_PREF = 2.5
W_FREE_BONUS = 0.5
W_RECENT_CATEGORY_PENALTY = -2.5
W_ALREADY_SHOWN_PENALTY = -1.5
W_JITTER = 1.2

# Secim, en iyi puana BU KADAR yakin adaylar arasindan yapilir. Sabit bir
# "ilk 5" arasindan rastgele secmek, kullanicinin acik sinyalini seyreltiyordu:
# "kahve" secildiginde eslesen 2 oneri varken isabet %25'e dusuyordu.
NEAR_SCORE = 1.5


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
    return config.current_wage()


def budget_band(budget_key: str) -> tuple[int, int | None]:
    """
    Kademenin alt ve ust siniri (TL).

    Alt sinir, "bu parayi gercekten kullanan" oneriyi ayirt etmek icin gerekli:
    tavan tek basina bir tercih degil, sadece bir kisittir.
    """
    tier = config.budget_tier(budget_key)
    if tier is None:
        return (0, 0)
    wage = config.current_wage()
    return (tier.amount_min(wage), tier.amount_max(wage))


def budget_ceiling(budget_key: str) -> int | None:
    """
    Kullanicinin sectigi kademenin ust siniri (TL). None = sinir yok.

    Bolum 8.2 ekonomik gerceklik kurali: kullanici butce belirtmediyse
    varsayilan 'bedava'dir ve ucretli oneri HIC gosterilmez.
    """
    tier = config.budget_tier(budget_key)
    if tier is None:
        return 0  # taninmayan kademe -> en guvenli taraf: bedava
    return tier.amount_max(config.current_wage())


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
    keywords = {k.lower() for k in ctx.keywords}
    pool = []
    for item in qs:
        # 5) KIMINLE
        if ctx.companions and ctx.companions not in (item.companions or []):
            continue
        # 8) MEVSIM - bos liste = tum yil gecerli.
        season = item.seasonality or []
        if season and month not in season:
            continue
        # 9) KELIMELER - kullanicinin tek acik sinyali, bu yuzden SERT filtre.
        #    En az bir kelime tutmali. Hicbiri tutmazsa _relax() kelimeleri
        #    dusurur; kullanici bos ekran gormez ama alakasiz kart da gormez.
        if keywords:
            tags = {str(t).lower() for t in (item.tags or [])}
            if not (keywords & tags):
                continue
        pool.append(item)

    # 1b) BUTCE BANDI - tercih, ama kendi icinde geri cekilir.
    #     Ucretli bir kademe secildiyse o bandin icindeki oneriler VARSA
    #     yalnizca onlar gosterilir. Puan olarak birakildiginda tema ya da
    #     enerji eslesmesi bandi yenebiliyordu: "Iyi" (500-1500) secen
    #     kullaniciya 120 TL'lik oneri donuyordu.
    if ctx.budget_key != "bedava":
        band_lo, _ = budget_band(ctx.budget_key)
        bantta = [s for s in pool if s.cost_max > band_lo]
        if bantta:
            return bantta

    return pool


# --------------------------------------------------------------------------
# Yumusak puanlama
# --------------------------------------------------------------------------
def active_theme() -> WeeklyTheme | None:
    """Bolum 7.6: kullanicinin hic gormedigi haftalik tema."""
    return config.active_theme()


def score(
    suggestion: Suggestion,
    ctx: Context,
    theme: WeeklyTheme | None,
    recent_categories: set[str],
    shown_ids: set[int],
    band: tuple[int, int | None] = (0, 0),
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

    # Butce. Bedava kademede bedava one cikar; ucretli bir kademe SECILDIYSE
    # o parayi kullanan oneri one cikar, bedava olan geri duser. Tavan yine
    # sert filtredir (Bolum 11 kural 3): butce hicbir zaman asilmaz.
    if ctx.budget_key == "bedava":
        if suggestion.cost_max == 0:
            value += W_FREE_BONUS
    elif suggestion.cost_max == 0:
        # Bant filtresi bos donduginde (o bantta hic oneri yoksa) bedava
        # olanlar yine de geri plana dusmeli.
        value += W_FREE_WHEN_PAID

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


# Puani en iyiye yakin adaylar arasindan kac tanesinin ornekleneceği.
# Olculdu: 58 adaylik bedava havuzda 80 cekilis ->
#   top_n=5  : 13 farkli oneri, secimlerin %94'u haftalik temali
#   top_n=15 : 42 farkli oneri, %46 temali
#   top_n=25 : 45 farkli oneri, %26 temali
# Kelime isabeti her degerde %100 kaldi; cunku hassasiyeti saglayan sey bu
# kapak degil, kelime ve butce bandinin SERT filtre olmasi. Kapak yalnizca
# cesitliligi kirpiyordu. 15, temanin editoryal etkisini korurken havuzun
# buyuk kismini erisilebilir birakiyor.
DEFAULT_TOP_N = 15


def pick(ctx: Context, profile=None, *, top_n: int = DEFAULT_TOP_N) -> Suggestion | None:
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
            band = budget_band(current.budget_key)
            scored = sorted(
                (
                    (score(s, current, theme, recent_categories, shown_ids, band), s)
                    for s in pool
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )
            best = scored[0][0]
            yakin = [s for puan, s in scored if best - puan <= NEAR_SCORE]
            return random.choice(yakin[:top_n])
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
