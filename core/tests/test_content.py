"""
Icerik kurallari testleri.

Bu testler urun kararlarini kilitler: biri kirilirsa icerik degil, URUN
bozulmus demektir.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from core.models import BudgetTier
from core.validators import (
    check_location_independence,
    check_safety,
    check_shape,
    check_tone,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "suggestions.json"
ITEMS = json.loads(FIXTURE.read_text(encoding="utf-8"))


class SeedContentTests(TestCase):
    """Havuzdaki her oneri Bolum 4 ve Bolum 3 kurallarina uymali."""

    def test_havuz_bos_degil(self):
        self.assertGreaterEqual(len(ITEMS), 60)

    def test_sluglar_benzersiz(self):
        slugs = [i["slug"] for i in ITEMS]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_konum_bagimsizligi(self):
        """Sehir/ilce/isletme ismi geciyorsa oneri havuza giremez."""
        for item in ITEMS:
            with self.subTest(slug=item["slug"]):
                self.assertEqual(check_location_independence(item), [])

    def test_sekil_kurallari(self):
        """Baslik <=6 kelime, 2-3 adim, hook tek cumle, fallback dolu."""
        for item in ITEMS:
            with self.subTest(slug=item["slug"]):
                self.assertEqual(check_shape(item), [])

    def test_guvenlik_ve_ton(self):
        for item in ITEMS:
            with self.subTest(slug=item["slug"]):
                self.assertEqual(check_safety(item), [])
                self.assertEqual(check_tone(item), [])

    def test_her_onerinin_fallbacki_var(self):
        """Bolum 11 kural 5: fallback bos gecilemez."""
        for item in ITEMS:
            with self.subTest(slug=item["slug"]):
                self.assertTrue(item["fallback"].strip())

    def test_bedava_varsayilan_havuzu_yeterli(self):
        """Bolum 8.2: butce girilmediyse ucretli oneri hic gosterilmez.
        Bu yuzden bedava havuz tek basina tasiyabilecek buyuklukte olmali."""
        bedava = [i for i in ITEMS if i["cost_max"] == 0]
        self.assertGreaterEqual(len(bedava), 30)

    def test_mekanli_onerilerde_venue_type_var(self):
        for item in ITEMS:
            if item["place"] == "mekanli":
                with self.subTest(slug=item["slug"]):
                    self.assertTrue(item["venue_type"].strip())

    def test_tum_kategoriler_temsil_ediliyor(self):
        from core.models import Category

        kullanilan = {i["category"] for i in ITEMS}
        self.assertEqual(kullanilan, {c.value for c in Category})


class ValidatorTests(TestCase):
    """Denetleyicinin gercekten yakaladigini dogrula (yanlis pozitif degil)."""

    BASE = {
        "slug": "t", "title": "Bir şey yap", "hook": "Tek cümle.",
        "steps": ["a", "b"], "fallback": "olmazsa bu", "place": "ev",
        "venue_type": "", "cost_min": 0, "cost_max": 0,
        "duration_min": 10, "duration_max": 20, "required_items": [],
    }

    def test_il_adi_yakalanir(self):
        item = {**self.BASE, "steps": ["Ankara'da yürü", "dön"]}
        self.assertTrue(check_location_independence(item))

    def test_vapur_yakalanir(self):
        item = {**self.BASE, "hook": "Vapura bin."}
        self.assertTrue(check_location_independence(item))

    def test_uzun_baslik_yakalanir(self):
        item = {**self.BASE, "title": "bir iki üç dört beş altı yedi"}
        self.assertTrue(check_shape(item))

    def test_bos_fallback_yakalanir(self):
        item = {**self.BASE, "fallback": "  "}
        self.assertTrue(check_shape(item))

    def test_koc_dili_yakalanir(self):
        item = {**self.BASE, "hook": "Hayatını değiştir."}
        self.assertTrue(check_tone(item))

    def test_temiz_oneri_gecer(self):
        self.assertEqual(check_shape(self.BASE), [])
        self.assertEqual(check_location_independence(self.BASE), [])


class BudgetTierTests(TestCase):
    """Bolum 7.2: tutarlar hardcode DEGIL, asgari ucrete endeksli."""

    fixtures: list[str] = []

    def setUp(self):
        from django.core.management import call_command

        call_command("seed", verbosity=0)

    def test_kademeler_dokumandaki_tutarlari_uretiyor(self):
        wage = Decimal("28075")
        beklenen = {
            "bedava": "0 TL",
            "az": "~1-150 TL",
            "orta": "~150-500 TL",
            "iyi": "~500-1.500 TL",
            "bol": "1.500 TL+",
        }
        for key, metin in beklenen.items():
            with self.subTest(kademe=key):
                self.assertEqual(BudgetTier.objects.get(key=key).describe(wage), metin)

    def test_asgari_ucret_artinca_kademeler_kayar(self):
        """Tek satir guncellenince tum kademeler kendiliginden guncellenmeli."""
        eski = BudgetTier.objects.get(key="orta").amount_max(Decimal("28075"))
        yeni = BudgetTier.objects.get(key="orta").amount_max(Decimal("40000"))
        self.assertGreater(yeni, eski)
