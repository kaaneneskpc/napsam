"""
Seed verisini yukler: uzaktan ayarlar, butce kademeleri, haftalik temalar,
oneri havuzu.

Calistirma:
    python manage.py seed            # ekler / gunceller
    python manage.py seed --check    # hicbir sey yazmaz, sadece denetler
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import BudgetTier, RemoteConfig, Suggestion, WeeklyTheme
from core.validators import ContentError, find_duplicates, validate

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# Butce kademeleri (Bolum 7.2). Tutarlar DEGIL, oranlar saklanir; tutar
# runtime'da net asgari ucretle carpilarak bulunur.
BUDGET_TIERS = [
    {"key": "bedava", "label": "Bedava", "hint": "", "ratio_min": "0", "ratio_max": "0", "order": 0},
    {"key": "az", "label": "Az", "hint": "çay-simit", "ratio_min": "0", "ratio_max": "0.005343", "order": 1},
    {"key": "orta", "label": "Orta", "hint": "bir kahve, bir tatlı", "ratio_min": "0.005343", "ratio_max": "0.017809", "order": 2},
    {"key": "iyi", "label": "İyi", "hint": "dışarıda yemek", "ratio_min": "0.017809", "ratio_max": "0.053428", "order": 3},
    {"key": "bol", "label": "Bol", "hint": "etkinlik, aktivite", "ratio_min": "0.053428", "ratio_max": None, "order": 4},
]

REMOTE_CONFIG = [
    {
        "key": "net_minimum_wage",
        "value": 28075,
        "note": "2026 net asgari ücret (TL). Bütçe kademeleri buna endeksli — "
                "yılda bir bu satırı güncellemek yeter, kod dağıtımı gerekmez.",
    },
    {
        "key": "ai_daily_global_limit",
        "value": 200,
        "note": "Tüm kullanıcılar için günlük TOPLAM AI çağrısı tavanı. "
                "Maliyeti üstten kilitleyen asıl koruma budur; bu satırı "
                "düşürmek anında etkili olur, kod dağıtımı gerekmez.",
    },
    {
        "key": "ai_daily_ip_limit",
        "value": 15,
        "note": "IP başına günlük AI çağrısı tavanı. Ev/ofis ağları tek IP "
                "paylaştığı için çerez tavanından (5) daha geniş tutuldu.",
    },
]

# Bolum 7.6 - haftalik tema. Kullanici gormez, sadece siralamayi etkiler.
WEEKLY_THEMES = [
    {"key": "yuru", "label": "Bu hafta yürü"},
    {"key": "elini_kullan", "label": "Bu hafta elini kullan"},
    {"key": "ekransiz", "label": "Bu hafta ekransız bir akşam"},
    {"key": "duzen", "label": "Bu hafta bir şeyi düzene sok"},
]


class Command(BaseCommand):
    help = "NAPSAM seed verisini yükler ve içerik kurallarını denetler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true",
            help="Veritabanına yazma, sadece içerik kurallarını denetle.",
        )

    def handle(self, *args, **options):
        check_only = options["check"]
        loud = options.get("verbosity", 1) >= 1

        path = FIXTURES / "suggestions.json"
        if not path.exists():
            raise CommandError(f"Fixture bulunamadı: {path}")

        items = json.loads(path.read_text(encoding="utf-8"))

        # --- 1) Icerik denetimi -------------------------------------------
        problems: list[str] = []
        seen: set[str] = set()
        for item in items:
            slug = item.get("slug", "?")
            if slug in seen:
                problems.append(f"{slug}: yinelenen slug")
            seen.add(slug)
            try:
                validate(item, strict=True)
            except ContentError as exc:
                problems.append(str(exc))

        problems.extend(find_duplicates(items))

        if problems:
            self.stderr.write(self.style.ERROR(f"{len(problems)} içerik hatası:"))
            for p in problems:
                self.stderr.write(f"  ✗ {p}")
            raise CommandError("İçerik kuralları sağlanmadı; hiçbir şey yazılmadı.")

        if loud:
            self.stdout.write(self.style.SUCCESS(
            f"✓ {len(items)} öneri içerik denetiminden geçti "
            f"(konum bağımsızlık, şekil, güvenlik, ton)."
        ))

        if check_only:
            if loud:
                self.stdout.write("--check verildi, veritabanına yazılmadı.")
            return

        # --- 2) Yazma ------------------------------------------------------
        with transaction.atomic():
            for row in REMOTE_CONFIG:
                RemoteConfig.objects.update_or_create(
                    key=row["key"], defaults={"value": row["value"], "note": row["note"]}
                )

            for row in BUDGET_TIERS:
                BudgetTier.objects.update_or_create(
                    key=row["key"],
                    defaults={
                        "label": row["label"],
                        "hint": row["hint"],
                        "ratio_min": Decimal(row["ratio_min"]),
                        "ratio_max": Decimal(row["ratio_max"]) if row["ratio_max"] is not None else None,
                        "order": row["order"],
                        "is_active": True,
                    },
                )

            # Temalar bu haftadan geriye dogru haftalik olarak yerlestirilir;
            # boylece motor her zaman gecerli bir tema bulur.
            today = date.today()
            monday = today - timedelta(days=today.weekday())
            for index, row in enumerate(WEEKLY_THEMES):
                WeeklyTheme.objects.update_or_create(
                    key=row["key"],
                    defaults={
                        "label": row["label"],
                        "starts_on": monday - timedelta(weeks=index),
                        "is_active": True,
                    },
                )

            # Toplu yazma. Satir satir update_or_create her oneri icin ~2 sorgu
            # demekti; sunucusuz havuza ~70 ms gidis-donusle 246 oneride islem
            # dakikalarca acik kaldi ve havuz baglantiyi kesti (islem geri
            # alindi). Simdi oneri sayisindan bagimsiz olarak birkac sorgu.
            slugs = [item["slug"] for item in items]
            existing = {s.slug: s for s in Suggestion.objects.filter(slug__in=slugs)}

            to_create: list[Suggestion] = []
            to_update: list[Suggestion] = []
            update_fields: set[str] = set()

            for item in items:
                data = dict(item)
                slug = data.pop("slug")
                data["source"] = "seed"
                data["is_active"] = True
                update_fields.update(data.keys())

                obj = existing.get(slug)
                if obj is None:
                    to_create.append(Suggestion(slug=slug, **data))
                else:
                    for field, value in data.items():
                        setattr(obj, field, value)
                    to_update.append(obj)

            Suggestion.objects.bulk_create(to_create, batch_size=200)
            if to_update:
                Suggestion.objects.bulk_update(
                    to_update, fields=sorted(update_fields), batch_size=100
                )
            created, updated = len(to_create), len(to_update)

        if loud:
            self.stdout.write(self.style.SUCCESS(
            f"✓ Yazıldı: {created} yeni, {updated} güncellenen öneri · "
            f"{len(BUDGET_TIERS)} bütçe kademesi · {len(WEEKLY_THEMES)} haftalık tema."
        ))
