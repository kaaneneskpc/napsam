"""Motor ve API testleri."""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core import engine, textmode
from core.models import AnonProfile, StateStatus, Suggestion, SuggestionState


def at(hour: int) -> datetime:
    return timezone.localtime().replace(hour=hour, minute=0, second=0, microsecond=0)


class EngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    # --- butce ---------------------------------------------------------
    def test_bedava_sadece_sifir_maliyetli_doner(self):
        """Bolum 8.2: butce girilmediyse ucretli oneri HIC gosterilmez."""
        ctx = engine.Context(budget_key="bedava", now=at(14))
        for item in engine.candidates(ctx):
            self.assertEqual(item.cost_max, 0, item.slug)

    def test_butce_tavani_asilmaz(self):
        ctx = engine.Context(budget_key="orta", now=at(14))
        tavan = engine.budget_ceiling("orta")
        for item in engine.candidates(ctx):
            self.assertLessEqual(item.cost_max, tavan, item.slug)

    def test_taninmayan_kademe_bedavaya_duser(self):
        """Guvenli taraf: bilinmeyen deger para harcatmamali."""
        self.assertEqual(engine.budget_ceiling("uydurma-kademe"), 0)

    def test_ust_kademe_alt_kademeyi_kapsar(self):
        az = {s.slug for s in engine.candidates(engine.Context(budget_key="az", now=at(14)))}
        bol = {s.slug for s in engine.candidates(engine.Context(budget_key="bol", now=at(14)))}
        self.assertTrue(az.issubset(bol))

    # --- saat ve guvenlik ----------------------------------------------
    def test_gece_tek_basina_riskli_oneri_dusuyor(self):
        """Bolum 11 kural 8."""
        ctx = engine.Context(budget_key="bol", now=at(2))
        for item in engine.candidates(ctx):
            self.assertFalse(item.night_unsafe_solo, item.slug)

    def test_gece_yanindaysa_riskli_oneri_kalabilir(self):
        """Kural tek basinayken gecerli; yanindakiler varsa kisit kalkar."""
        yalniz = engine.candidates(engine.Context(budget_key="bol", now=at(2)))
        yanimda = engine.candidates(
            engine.Context(budget_key="bol", now=at(2), companions="friends")
        )
        self.assertFalse(any(s.night_unsafe_solo for s in yalniz))
        self.assertTrue(any(s.night_unsafe_solo for s in yanimda))

    def test_gece_kapali_mekanlar_onerilmiyor(self):
        ctx = engine.Context(budget_key="bol", now=at(3))
        for item in engine.candidates(ctx):
            self.assertNotEqual(item.place, "mekanli", item.slug)

    # --- diger sert filtreler -------------------------------------------
    def test_sure_filtresi(self):
        ctx = engine.Context(budget_key="bol", duration_max=25, now=at(14))
        for item in engine.candidates(ctx):
            self.assertLessEqual(item.duration_min, 25, item.slug)

    def test_kiminle_filtresi(self):
        ctx = engine.Context(budget_key="bol", companions="kids", now=at(14))
        pool = engine.candidates(ctx)
        self.assertTrue(pool)
        for item in pool:
            self.assertIn("kids", item.companions, item.slug)

    def test_evde_filtresi(self):
        ctx = engine.Context(budget_key="bol", place_pref="ev", now=at(14))
        for item in engine.candidates(ctx):
            self.assertEqual(item.place, "ev", item.slug)

    def test_yagmurda_acik_hava_onerilmiyor(self):
        ctx = engine.Context(
            budget_key="bol", now=at(14), weather={"condition": "rain", "tempC": 12}
        )
        for item in engine.candidates(ctx):
            self.assertNotIn(item.weather_need, {"dry", "warm"}, item.slug)

    def test_excluded_saygi_goruyor(self):
        ilk = engine.pick(engine.Context(now=at(14)))
        ctx = engine.Context(now=at(14), excluded={ilk.slug})
        for _ in range(10):
            self.assertNotEqual(engine.pick(ctx).slug, ilk.slug)

    # --- secim ----------------------------------------------------------
    def test_pick_her_zaman_bir_sey_dondurur(self):
        """Bos ekran, kullanicinin Instagram'a donmesi demektir."""
        zor = engine.Context(
            budget_key="bedava", now=at(3), duration_max=5,
            companions="kids", energy="high", place_pref="disari",
            keywords=["olmayan-etiket"],
        )
        self.assertIsNotNone(engine.pick(zor))

    def test_kelimeler_siralamayi_etkiliyor(self):
        ctx = engine.Context(budget_key="bedava", keywords=["yemek"], now=at(14))
        secilenler = {engine.pick(ctx).category for _ in range(25)}
        self.assertTrue(secilenler & {"mutfak", "yeme_icme"})

    def test_gecilen_oneri_cooldown_suresince_gelmiyor(self):
        profile = AnonProfile.objects.create()
        hedef = Suggestion.objects.active().filter(cost_max=0).first()
        engine.mark_dismissed(profile, hedef)
        ctx = engine.Context(budget_key="bedava", now=at(14))
        havuz = {s.slug for s in engine.candidates(ctx, profile)}
        self.assertNotIn(hedef.slug, havuz)


class TextModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def test_param_yok_bedavaya_cevriliyor(self):
        self.assertEqual(textmode.parse("3 saatim var param yok")["budget_key"], "bedava")

    def test_sure_cikariliyor(self):
        self.assertEqual(textmode.parse("3 saatim var param yok")["duration_max"], 180)
        self.assertEqual(textmode.parse("yarım saatim var")["duration_max"], 30)
        self.assertEqual(textmode.parse("40 dakikam var")["duration_max"], 40)

    def test_kiminle_cikariliyor(self):
        self.assertEqual(textmode.parse("sevgilimle bir şey yapalım")["companions"], "couple")
        self.assertEqual(textmode.parse("arkadaşımla takılacağız")["companions"], "friends")

    def test_yorgunluk_dusuk_enerjiye_cevriliyor(self):
        self.assertEqual(textmode.parse("çok yorgunum")["energy"], "low")

    def test_anahtar_kelime_eslesmesi(self):
        self.assertIn("dışarı", textmode.parse("canım biraz deniz gördü")["keywords"])

    def test_tl_tutari_kademeye_eslesiyor(self):
        self.assertEqual(textmode.parse("200 tl bütçem var")["budget_key"], "orta")

    def test_bos_metin_bos_baglam(self):
        self.assertEqual(textmode.parse("   "), {})


@override_settings(GEMINI_API_KEY="")   # testler aga cikmaz
class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    # --- gizlilik -------------------------------------------------------
    def test_ana_ekran_veritabanina_yazmiyor(self):
        """
        Bolum 3 kural 3 + Bolum 15: sadece acip cikan ziyaretci icin
        tek bir kayit bile olusmamali.
        """
        self.client.get("/")
        self.assertEqual(AnonProfile.objects.count(), 0)

    def test_oneri_istegi_de_yazmiyor(self):
        self.post("/api/suggest/", {"budget": "bedava"})
        self.assertEqual(AnonProfile.objects.count(), 0)

    def test_profil_ancak_kaydedince_olusuyor(self):
        slug = Suggestion.objects.first().slug
        self.post("/api/state/", {"slug": slug, "action": "save"})
        self.assertEqual(AnonProfile.objects.count(), 1)

    # --- davranis -------------------------------------------------------
    def test_ana_ekran_hazir_oneriyle_aciliyor(self):
        """Acilistan oneriye 0 dokunus."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["suggestion"])

    def test_suggest_kart_donuyor(self):
        response = self.post("/api/suggest/", {"mode": "quick", "budget": "bedava"})
        self.assertEqual(response.status_code, 200)
        card = response.json()["suggestion"]
        self.assertTrue(card["title"])
        self.assertTrue(card["fallback"])
        self.assertIn(len(card["steps"]), (2, 3))

    def test_suggest_serbest_metni_anliyor(self):
        response = self.post("/api/suggest/", {
            "mode": "text", "budget": "bol", "text": "param yok evde takılayım"
        })
        data = response.json()
        self.assertEqual(data["meta"]["budget"], "bedava")
        self.assertEqual(data["suggestion"]["badges"]["cost"], "₺0 Bedava")

    def test_kaydet_ve_kaldir(self):
        slug = Suggestion.objects.first().slug
        self.assertEqual(self.post("/api/state/", {"slug": slug, "action": "save"})
                         .json()["savedCount"], 1)
        self.assertEqual(self.post("/api/state/", {"slug": slug, "action": "unsave"})
                         .json()["savedCount"], 0)

    def test_yapiyorum_isaretleniyor(self):
        slug = Suggestion.objects.first().slug
        self.post("/api/state/", {"slug": slug, "action": "done"})
        state = SuggestionState.objects.get(suggestion__slug=slug)
        self.assertEqual(state.status, StateStatus.DONE)
        self.assertIsNotNone(state.done_at)

    def test_bilinmeyen_slug_404(self):
        self.assertEqual(
            self.post("/api/state/", {"slug": "yok-boyle", "action": "save"}).status_code, 404
        )

    def test_gecersiz_eylem_400(self):
        slug = Suggestion.objects.first().slug
        self.assertEqual(
            self.post("/api/state/", {"slug": slug, "action": "sil-her-seyi"}).status_code, 400
        )

    def test_bozuk_json_400(self):
        self.assertEqual(
            self.client.post("/api/suggest/", data="{bozuk", content_type="application/json")
            .status_code, 400
        )

    def test_kaydedilenler_sayfasi_aciliyor(self):
        self.assertEqual(self.client.get("/kaydedilenler/").status_code, 200)

    def test_gizlilik_sayfasi_aciliyor(self):
        self.assertEqual(self.client.get("/gizlilik/").status_code, 200)

    def test_healthz(self):
        data = self.client.get("/healthz/").json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["suggestions"], 60)
