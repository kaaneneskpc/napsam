"""
NAPSAM veri modeli (napsam-app-prompt.md Bolum 9).

Iki bilincli eksiklik var, ikisi de urun karari:
  1. Sehir / ilce / semt alani YOK. Bir oneri Turkiye'nin her yerinde
     yapilabilir olmak zorunda (Bolum 4). Konum runtime'da bile saklanmaz.
  2. Butce tutarlari koda gomulu DEGIL. Enflasyonla 6 ayda eskidigi icin
     BudgetTier + RemoteConfig uzerinden asgari ucrete endeksli tutulur
     (Bolum 7.2 TEKNIK KURAL).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# --------------------------------------------------------------------------
# Secim kumeleri
# --------------------------------------------------------------------------
class Category(models.TextChoices):
    """Bolum 8.1 taksonomisi. Genisletilebilir ama daraltilmamali."""

    EV_ICI = "ev_ici", "Ev içi"
    MUTFAK = "mutfak", "Mutfak"
    DISARI_YAKIN = "disari_yakin", "Dışarı / yakın"
    YEME_ICME = "yeme_icme", "Yeme-içme"
    HAREKET = "hareket", "Hareket"
    KULTUR = "kultur", "Kültür"
    SOSYAL = "sosyal", "Sosyal"
    OGRENME = "ogrenme", "Öğrenme"
    URETME = "uretme", "Üretme"
    DOGA = "doga", "Doğa"
    KENDINE_BAKIM = "kendine_bakim", "Kendine bakım"
    IYILIK = "iyilik", "İyilik"
    OYUN = "oyun", "Oyun"
    UFAK_ISLER = "ufak_isler", "Ufak işler"


class Place(models.TextChoices):
    """Bolum 4'teki uc kap. Sehir degil, MEKAN TURU ayrimi."""

    EV = "ev", "Evde"
    YAKIN = "yakin", "Yürüme mesafesinde"
    MEKANLI = "mekanli", "Belirli tipte mekan gerekir"


class Energy(models.TextChoices):
    LOW = "low", "Düşük"
    MEDIUM = "medium", "Orta"
    HIGH = "high", "Yüksek"


class WeatherNeed(models.TextChoices):
    ANY = "any", "Fark etmez"
    DRY = "dry", "Yağışsız olmalı"
    WARM = "warm", "Ilık olmalı"
    INDOOR = "indoor", "Kapalı alan"


class Companion(models.TextChoices):
    SOLO = "solo", "Tek başına"
    COUPLE = "couple", "Çift"
    FRIENDS = "friends", "Arkadaşlarla"
    FAMILY = "family", "Ailece"
    KIDS = "kids", "Çocuklu"


class SuggestionSource(models.TextChoices):
    SEED = "seed", "Seed"
    AI = "ai", "AI"


class StateStatus(models.TextChoices):
    SHOWN = "shown", "Gösterildi"
    SAVED = "saved", "Kaydedildi"
    DISMISSED = "dismissed", "Geçildi"
    DONE = "done", "Yapıldı"


# --------------------------------------------------------------------------
# Uzaktan yapilandirma
# --------------------------------------------------------------------------
class RemoteConfig(models.Model):
    """
    Kod dagitimi gerektirmeden degisebilen tekil degerler.

    En kritik anahtar: `net_minimum_wage`. Butce kademeleri buna endeksli
    oldugu icin yilda bir bu satiri guncellemek tum kademeleri gunceller.
    """

    key = models.CharField(max_length=64, primary_key=True)
    value = models.JSONField()
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Uzaktan ayar"
        verbose_name_plural = "Uzaktan ayarlar"

    def __str__(self) -> str:
        return f"{self.key} = {self.value}"

    @classmethod
    def get(cls, key: str, default=None):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default


class BudgetTier(models.Model):
    """
    Butce kademesi (Bolum 7.2).

    Tutarlar `ratio_*` alanlarindan runtime'da hesaplanir:
        tutar = ratio * net_asgari_ucret
    Boylece asgari ucret guncellendiginde kademeler kendiliginden kayar.
    `ratio_max` bos ise ust sinir yoktur ("Bol").
    """

    key = models.SlugField(max_length=32, primary_key=True)
    label = models.CharField(max_length=32)
    hint = models.CharField(max_length=64, blank=True, help_text="Ör: 'çay-simit'")
    ratio_min = models.DecimalField(max_digits=8, decimal_places=6, default=Decimal("0"))
    ratio_max = models.DecimalField(
        max_digits=8, decimal_places=6, null=True, blank=True,
        help_text="Boş = üst sınır yok",
    )
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        verbose_name = "Bütçe kademesi"
        verbose_name_plural = "Bütçe kademeleri"

    def __str__(self) -> str:
        return self.label

    # -- hesaplanan tutarlar --------------------------------------------
    @staticmethod
    def _round_to_10(amount: Decimal) -> int:
        return int((amount / 10).quantize(Decimal("1")) * 10)

    def amount_min(self, wage: Decimal) -> int:
        return self._round_to_10(self.ratio_min * wage)

    def amount_max(self, wage: Decimal) -> int | None:
        if self.ratio_max is None:
            return None
        return self._round_to_10(self.ratio_max * wage)

    def describe(self, wage: Decimal) -> str:
        """Arayuzde kademenin altinda gorunen tutar metni."""
        low, high = self.amount_min(wage), self.amount_max(wage)
        if high is None:
            return f"{low:,}".replace(",", ".") + " TL+"
        if low == 0 and high == 0:
            return "0 TL"
        low_text = f"{max(low, 1):,}".replace(",", ".")
        high_text = f"{high:,}".replace(",", ".")
        return f"~{low_text}-{high_text} TL"


class WeeklyTheme(models.Model):
    """
    Bolum 7.6 - haftalik editoryal tema.

    Kullanici bu temayi HIC gormez; sadece siralamayi agirliklandirir.
    Sifir arayuz maliyetiyle tum kullanici tabanini tazeler.
    """

    key = models.SlugField(max_length=48, primary_key=True)
    label = models.CharField(max_length=80, help_text="Sadece yönetim için")
    starts_on = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-starts_on"]
        verbose_name = "Haftalık tema"
        verbose_name_plural = "Haftalık temalar"

    def __str__(self) -> str:
        return f"{self.starts_on} · {self.label}"


# --------------------------------------------------------------------------
# Oneri havuzu
# --------------------------------------------------------------------------
class SuggestionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def seeds(self):
        return self.filter(source=SuggestionSource.SEED)


class Suggestion(models.Model):
    """
    Tek bir somut oneri. Bolum 7.4 + Bolum 9.

    Zorunlu kurallar (clean() ile denetlenir):
      - steps: 2 veya 3 adim. Bos gecilemez.
      - fallback: bos gecilemez. "Plan tutmazsa ne yapilir?" cevapsiz kalirsa
        oneri basarisizdir.
      - place=mekanli ise venue_type dolu olmali; isletme ismi ASLA yazilmaz.
    """

    slug = models.SlugField(max_length=80, unique=True)
    source = models.CharField(
        max_length=8, choices=SuggestionSource.choices, default=SuggestionSource.SEED
    )

    title = models.CharField(max_length=60, help_text="En fazla 6 kelime, emir kipi")
    hook = models.CharField(max_length=160, help_text="Tek cümle — neden şimdi")
    steps = models.JSONField(default=list, help_text="2-3 adım, her biri tek satır")
    fallback = models.TextField(help_text="ZORUNLU: plan tutmazsa ne yapılır")

    category = models.CharField(max_length=24, choices=Category.choices)

    cost_min = models.PositiveIntegerField(default=0, help_text="TL")
    cost_max = models.PositiveIntegerField(default=0, help_text="TL")
    cost_note = models.CharField(max_length=80, blank=True)

    duration_min = models.PositiveSmallIntegerField(help_text="Dakika")
    duration_max = models.PositiveSmallIntegerField(help_text="Dakika")

    companions = models.JSONField(default=list, help_text="solo|couple|friends|family|kids")
    energy = models.CharField(max_length=8, choices=Energy.choices, default=Energy.MEDIUM)
    place = models.CharField(max_length=8, choices=Place.choices)
    venue_type = models.CharField(
        max_length=40, blank=True,
        help_text="place=mekanli ise mekan TÜRÜ (ör: 'sinema'). İşletme ismi yazma.",
    )

    weather_need = models.CharField(
        max_length=8, choices=WeatherNeed.choices, default=WeatherNeed.ANY
    )
    seasonality = models.JSONField(
        default=list, blank=True,
        help_text="Ay numaraları [1..12] veya boş = tüm yıl",
    )
    cultural_tags = models.JSONField(
        default=list, blank=True, help_text="ramazan, bayram, pazar_gunu, ay_sonu ..."
    )
    required_items = models.JSONField(default=list, blank=True, help_text="En fazla 3 madde")
    theme_tags = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True, help_text="Kelime modu eşleşmesi")

    # Gece gec saatte tek basina onerilmesi sakincali olan aktiviteler
    # (Bolum 11, kural 8) bu bayrakla havuzdan dusurulur.
    night_unsafe_solo = models.BooleanField(
        default=False, help_text="Gece geç saatte tek başına önerilmesin"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SuggestionQuerySet.as_manager()

    class Meta:
        ordering = ["slug"]
        verbose_name = "Öneri"
        verbose_name_plural = "Öneriler"
        indexes = [
            models.Index(fields=["is_active", "category"]),
            models.Index(fields=["is_active", "place"]),
            models.Index(fields=["is_active", "cost_max"]),
        ]

    def __str__(self) -> str:
        return self.title

    # -- dogrulama -------------------------------------------------------
    def clean(self):
        errors: dict[str, str] = {}

        if not isinstance(self.steps, list) or not (2 <= len(self.steps) <= 3):
            errors["steps"] = "2 veya 3 adım olmalı."
        elif any(not str(s).strip() for s in self.steps):
            errors["steps"] = "Adımlar boş olamaz."

        if not str(self.fallback).strip():
            errors["fallback"] = "Zorunlu: plan tutmazsa ne yapılacağı yazılmalı."

        if len(self.title.split()) > 6:
            errors["title"] = "Başlık en fazla 6 kelime olmalı."

        if self.place == Place.MEKANLI and not self.venue_type.strip():
            errors["venue_type"] = "place=mekanli ise mekan türü zorunlu."

        if self.cost_max < self.cost_min:
            errors["cost_max"] = "Üst maliyet alt maliyetten küçük olamaz."

        if self.duration_max < self.duration_min:
            errors["duration_max"] = "Üst süre alt süreden küçük olamaz."

        if len(self.required_items or []) > 3:
            errors["required_items"] = "En fazla 3 madde."

        if errors:
            raise ValidationError(errors)

    # -- yardimcilar -----------------------------------------------------
    @property
    def is_free(self) -> bool:
        return self.cost_max == 0

    @property
    def cost_badge(self) -> str:
        if self.is_free:
            return "₺0 Bedava"
        if self.cost_note:
            return f"~₺{self.cost_max} {self.cost_note}"
        return f"~₺{self.cost_max}"

    @property
    def duration_badge(self) -> str:
        if self.duration_min == self.duration_max:
            return f"{self.duration_max} dk"
        return f"{self.duration_min}-{self.duration_max} dk"

    @property
    def place_badge(self) -> str:
        if self.place == Place.EV:
            return "Evde"
        if self.place == Place.YAKIN:
            return "Dışarı / Yakın"
        return self.venue_type.capitalize() or "Mekanlı"

    @property
    def companion_badge(self) -> str:
        labels = dict(Companion.choices)
        if not self.companions or len(self.companions) >= 4:
            return "Farketmez"
        return " / ".join(labels.get(c, c) for c in self.companions[:2])


# --------------------------------------------------------------------------
# Anonim kullanici ve durum
# --------------------------------------------------------------------------
class AnonProfile(models.Model):
    """
    Anonim kullanici (Bolum 3, kural 3 + Bolum 15).

    Kisisel veri tasimaz: sadece cerezdeki rastgele UUID. E-posta, ad, konum
    ya da cihaz kimligi saklanmaz.
    """

    anon_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    budget_tier_default = models.ForeignKey(
        BudgetTier, null=True, blank=True, on_delete=models.SET_NULL
    )
    disliked_categories = models.JSONField(default=list, blank=True)
    locale = models.CharField(max_length=8, default="tr")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Anonim kullanıcı"
        verbose_name_plural = "Anonim kullanıcılar"

    def __str__(self) -> str:
        return str(self.anon_id)


class SuggestionState(models.Model):
    """Bir kullanicinin bir oneriyle iliskisi (Bolum 9 UserSuggestionState)."""

    profile = models.ForeignKey(AnonProfile, on_delete=models.CASCADE, related_name="states")
    suggestion = models.ForeignKey(Suggestion, on_delete=models.CASCADE, related_name="states")
    status = models.CharField(
        max_length=12, choices=StateStatus.choices, default=StateStatus.SHOWN
    )
    dismissed_until = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    shown_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "suggestion"], name="uniq_profile_suggestion"
            )
        ]
        indexes = [models.Index(fields=["profile", "status"])]
        verbose_name = "Öneri durumu"
        verbose_name_plural = "Öneri durumları"

    def __str__(self) -> str:
        return f"{self.profile_id} · {self.suggestion_id} · {self.status}"

    @property
    def is_cooling_down(self) -> bool:
        return bool(self.dismissed_until and self.dismissed_until > timezone.now())


# --------------------------------------------------------------------------
# AI katmani (Bolum 10, Katman 2)
# --------------------------------------------------------------------------
class AiUsage(models.Model):
    """
    Gunluk AI cagri sayaci.

    AnonProfile'a bagli DEGIL, dogrudan cerezdeki anon_id'ye bagli. Boylece
    yalnizca serbest metin yazan bir ziyaretci icin bile kalici bir profil
    satiri olusturmak gerekmez.
    """

    anon_id = models.UUIDField()
    day = models.DateField()
    count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["anon_id", "day"], name="uniq_ai_usage_day")
        ]
        verbose_name = "AI kullanımı"
        verbose_name_plural = "AI kullanımları"

    def __str__(self) -> str:
        return f"{self.anon_id} · {self.day} · {self.count}"


class AiRequestCache(models.Model):
    """
    Filtre imzasi -> uretilmis oneri (Bolum 10: 7 gun TTL).

    Ayni baglam icin modeli tekrar cagirmak hem para hem sure kaybi.
    """

    signature = models.CharField(max_length=64, primary_key=True)
    suggestion = models.ForeignKey(Suggestion, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "AI önbelleği"
        verbose_name_plural = "AI önbellekleri"

    def __str__(self) -> str:
        return self.signature[:12]
