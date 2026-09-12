"""
AI katmani testleri.

Hicbiri aga cikmaz; requests.post taklit edilir. Amac model kalitesini degil,
ETRAFINDAKI KORUMALARI dogrulamak: son denetim, butce tavani, onbellek,
gunluk kota ve her basarisizlikta seed havuzuna sessizce dusme.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core import ai, engine
from core.models import AiRequestCache, AiUsage, Suggestion, SuggestionSource

GECERLI_CIKTI = {
    "title": "En yakın parka yürü",
    "hook": "Ekrandan yarım saat uzaklaşmak için en kısa yol.",
    "steps": [
        "Telefonu sessize alıp cebine koy.",
        "En yakın yeşil alana doğru yürü.",
        "Yirmi dakika oturup etrafı izle.",
    ],
    "fallback": "Yağmur varsa: Pencere önüne sandalye çek, on dakika dışarıyı izle.",
    "category": "disari_yakin",
    "costMin": 0, "costMax": 0, "costNote": "",
    "durationMin": 30, "durationMax": 60,
    "energy": "low", "place": "yakin", "venueType": "",
    "requiredItems": [],
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def yanit(ciktı: dict) -> FakeResponse:
    return FakeResponse({"output_text": json.dumps(ciktı, ensure_ascii=False)})


@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
class AiGenerateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def setUp(self):
        self.ctx = engine.Context(budget_key="bedava", now=timezone.localtime())
        self.anon = uuid.uuid4()

    def uret(self, ciktı, tavan=0):
        with patch("core.ai.requests.post", return_value=yanit(ciktı)) as post:
            sonuc = ai.generate(self.ctx, "param yok canım sıkıldı", self.anon, tavan)
        return sonuc, post

    # --- mutlu yol ------------------------------------------------------
    def test_gecerli_cikti_oneriye_donusuyor(self):
        sonuc, post = self.uret(GECERLI_CIKTI)
        self.assertIsNotNone(sonuc)
        self.assertEqual(sonuc.title, "En yakın parka yürü")
        self.assertEqual(sonuc.source, SuggestionSource.AI)
        self.assertEqual(post.call_count, 1)

    def test_anahtar_istemciye_degil_basliga_konuyor(self):
        _, post = self.uret(GECERLI_CIKTI)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-goog-api-key"], "test-key")

    def test_baglamda_konum_alani_yok(self):
        """
        Bolum 4: konum hicbir zaman modele gonderilmez.

        Yalnizca BAGLAM JSON'u denetlenir; kural metninde "şehir ... yazma"
        ifadesinin gecmesi zaten istenen seydir.
        """
        _, post = self.uret(GECERLI_CIKTI)
        girdi = post.call_args.kwargs["json"]["input"]
        baglam = girdi.split("KULLANICI BAĞLAMI:", 1)[1].lower()
        for yasak in ("city", "şehir", "district", "ilçe", "lat", "lon", "konum", "coord"):
            self.assertNotIn(yasak, baglam)

    # --- son denetim ----------------------------------------------------
    def test_sehir_ismi_sizan_cikti_reddediliyor(self):
        bozuk = {**GECERLI_CIKTI, "steps": ["İstanbul'da sahile yürü", "dön", "otur"]}
        sonuc, _ = self.uret(bozuk)
        self.assertIsNone(sonuc)

    def test_vapur_gibi_yere_bagimli_cikti_reddediliyor(self):
        bozuk = {**GECERLI_CIKTI, "hook": "Vapura binip karşıya geç."}
        sonuc, _ = self.uret(bozuk)
        self.assertIsNone(sonuc)

    def test_butceyi_asan_cikti_reddediliyor(self):
        pahali = {**GECERLI_CIKTI, "costMax": 500}
        sonuc, _ = self.uret(pahali, tavan=0)
        self.assertIsNone(sonuc)

    def test_yasakli_icerik_reddediliyor(self):
        bozuk = {**GECERLI_CIKTI, "hook": "Bir bira aç ve otur."}
        sonuc, _ = self.uret(bozuk)
        self.assertIsNone(sonuc)

    def test_uzun_baslik_reddediliyor(self):
        bozuk = {**GECERLI_CIKTI, "title": "bir iki üç dört beş altı yedi sekiz"}
        sonuc, _ = self.uret(bozuk)
        self.assertIsNone(sonuc)

    def test_bos_fallback_reddediliyor(self):
        bozuk = {**GECERLI_CIKTI, "fallback": "   "}
        sonuc, _ = self.uret(bozuk)
        self.assertIsNone(sonuc)

    # --- normallestirme -------------------------------------------------
    def test_gereksiz_venue_type_temizleniyor(self):
        """Kucuk tutarsizlik oneriyi cope atmamali, duzeltilmeli."""
        ciktı = {**GECERLI_CIKTI, "place": "yakin", "venueType": "sahil parkı"}
        sonuc, _ = self.uret(ciktı)
        self.assertIsNotNone(sonuc)
        self.assertEqual(sonuc.venue_type, "")

    def test_mekan_turu_yoksa_mekanli_iddiasi_dusuyor(self):
        ciktı = {**GECERLI_CIKTI, "place": "mekanli", "venueType": ""}
        sonuc, _ = self.uret(ciktı)
        self.assertIsNotNone(sonuc)
        self.assertEqual(sonuc.place, "yakin")

    def test_ters_sure_araligi_duzeltiliyor(self):
        ciktı = {**GECERLI_CIKTI, "durationMin": 90, "durationMax": 30}
        sonuc, _ = self.uret(ciktı)
        self.assertGreaterEqual(sonuc.duration_max, sonuc.duration_min)

    # --- dayaniklilik ---------------------------------------------------
    def test_http_hatasinda_none(self):
        with patch("core.ai.requests.post", return_value=FakeResponse({"error": "x"}, 500)):
            self.assertIsNone(ai.generate(self.ctx, "bir şey", self.anon, 0))

    def test_json_olmayan_cikti_none(self):
        with patch("core.ai.requests.post",
                   return_value=FakeResponse({"output_text": "merhaba, JSON değilim"})):
            self.assertIsNone(ai.generate(self.ctx, "bir şey", self.anon, 0))

    def test_ag_hatasinda_none(self):
        import requests

        with patch("core.ai.requests.post", side_effect=requests.Timeout()):
            self.assertIsNone(ai.generate(self.ctx, "bir şey", self.anon, 0))

    def test_kod_blogu_sarmali_soyuluyor(self):
        sarmalı = "```json\n" + json.dumps(GECERLI_CIKTI, ensure_ascii=False) + "\n```"
        with patch("core.ai.requests.post",
                   return_value=FakeResponse({"output_text": sarmalı})):
            self.assertIsNotNone(ai.generate(self.ctx, "bir şey", self.anon, 0))

    def test_steps_icindeki_metin_de_okunuyor(self):
        """Yanit sekli degisirse de metin bulunabilmeli."""
        payload = {"steps": [{"content": [
            {"text": json.dumps(GECERLI_CIKTI, ensure_ascii=False)}
        ]}]}
        with patch("core.ai.requests.post", return_value=FakeResponse(payload)):
            self.assertIsNotNone(ai.generate(self.ctx, "bir şey", self.anon, 0))

    # --- onbellek ve kota -----------------------------------------------
    def test_ayni_baglam_ikinci_kez_modeli_cagirmiyor(self):
        with patch("core.ai.requests.post", return_value=yanit(GECERLI_CIKTI)) as post:
            ilk = ai.generate(self.ctx, "aynı istek", self.anon, 0)
            ikinci = ai.generate(self.ctx, "aynı istek", self.anon, 0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(ilk.pk, ikinci.pk)
        self.assertEqual(AiRequestCache.objects.count(), 1)

    def test_gunluk_kota_bitince_model_cagrilmiyor(self):
        AiUsage.objects.create(
            scope=f"anon:{self.anon}", day=timezone.localdate(), count=99
        )
        with patch("core.ai.requests.post") as post:
            self.assertIsNone(ai.generate(self.ctx, "bir şey", self.anon, 0))
        post.assert_not_called()

    def test_her_cagri_kotadan_dusuyor(self):
        onceki = ai.quota_left(self.anon)
        self.uret(GECERLI_CIKTI)
        self.assertEqual(ai.quota_left(self.anon), onceki - 1)

    # --- havuz ayrimi ---------------------------------------------------
    def test_ai_onerisi_seed_havuzuna_karismiyor(self):
        sonuc, _ = self.uret(GECERLI_CIKTI)
        havuz = {s.slug for s in engine.candidates(engine.Context(budget_key="bol"))}
        self.assertNotIn(sonuc.slug, havuz)

    def test_ai_onerisi_yine_de_kaydedilebiliyor(self):
        sonuc, _ = self.uret(GECERLI_CIKTI)
        response = self.client.post(
            "/api/state/",
            data=json.dumps({"slug": sonuc.slug, "action": "save"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)


class AiDisabledTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    @override_settings(GEMINI_API_KEY="")
    def test_anahtar_yoksa_katman_kapali(self):
        self.assertFalse(ai.is_enabled())
        self.assertIsNone(
            ai.generate(engine.Context(), "bir şey", uuid.uuid4(), 0)
        )

    @override_settings(GEMINI_API_KEY="")
    def test_anahtar_yoksa_uygulama_yine_de_oneri_veriyor(self):
        """AI bir sus; cekirdek degil."""
        response = self.client.post(
            "/api/state/", data="{}", content_type="application/json"
        )
        self.assertIn(response.status_code, (400, 404))

        response = self.client.post(
            "/api/suggest/",
            data=json.dumps({"mode": "text", "text": "param yok evde takılayım"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["suggestion"])

    @override_settings(GEMINI_API_KEY="test-key")
    def test_ai_basarisiz_olursa_seed_havuzuna_dusuyor(self):
        with patch("core.ai.requests.post", return_value=FakeResponse({}, 500)):
            response = self.client.post(
                "/api/suggest/",
                data=json.dumps({"mode": "text", "text": "param yok canım sıkıldı"}),
                content_type="application/json",
            )
        data = response.json()
        self.assertIsNotNone(data["suggestion"])
        self.assertEqual(data["suggestion"]["source"], "seed")


@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
class AiCostGuardTests(TestCase):
    """
    Maliyet korumalari.

    API anahtari proje sahibinindir, cagriyi yapan ziyaretcidir. Cerez tabanli
    sayac tek basina koruma DEGILDIR: cerez silmek ya da gizli sekme acmak onu
    sifirlar. Bu testler global ve IP tavanlarinin gercekten baglayici
    oldugunu kilitler.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.ctx = engine.Context(budget_key="bedava", now=timezone.localtime())

    def test_global_tavan_yeni_cerezi_de_durduruyor(self):
        """Cerezi silip yeniden gelmek global tavani asmaya yaramamali."""
        AiUsage.objects.create(
            scope=ai.GLOBAL_SCOPE, day=timezone.localdate(),
            count=ai.global_daily_limit(),
        )
        with patch("core.ai.requests.post") as post:
            for _ in range(3):
                taze_cerez = uuid.uuid4()          # her seferinde yepyeni kimlik
                self.assertIsNone(
                    ai.generate(self.ctx, "bir şey", taze_cerez, 0, ip_hash="abc")
                )
        post.assert_not_called()

    def test_ip_tavani_baglayici(self):
        AiUsage.objects.create(
            scope="ip:sabit-ip", day=timezone.localdate(), count=ai.ip_daily_limit(),
        )
        with patch("core.ai.requests.post") as post:
            self.assertIsNone(
                ai.generate(self.ctx, "bir şey", uuid.uuid4(), 0, ip_hash="sabit-ip")
            )
        post.assert_not_called()

    def test_kalan_hak_en_dar_kapsamdan_geliyor(self):
        anon = uuid.uuid4()
        AiUsage.objects.create(scope=f"anon:{anon}", day=timezone.localdate(), count=4)
        # cerez tavani 5 -> 1 kaldi; global ve IP bos ama en dar olan gecerli
        self.assertEqual(ai.quota_left(anon, "bos-ip"), 1)

    def test_basarili_cagri_uc_sayaci_da_artiriyor(self):
        anon = uuid.uuid4()
        with patch("core.ai.requests.post", return_value=yanit(GECERLI_CIKTI)):
            ai.generate(self.ctx, "bir şey", anon, 0, ip_hash="ip1")
        kapsamlar = set(AiUsage.objects.values_list("scope", flat=True))
        self.assertEqual(kapsamlar, {ai.GLOBAL_SCOPE, f"anon:{anon}", "ip:ip1"})

    def test_tavan_panelden_dusurulebiliyor(self):
        """Maliyet kontrolu kod dagitimi beklemeden devreye girmeli."""
        from core.models import RemoteConfig

        RemoteConfig.objects.update_or_create(
            key="ai_daily_global_limit", defaults={"value": 0}
        )
        with patch("core.ai.requests.post") as post:
            self.assertIsNone(ai.generate(self.ctx, "bir şey", uuid.uuid4(), 0))
        post.assert_not_called()

    def test_ip_acik_saklanmiyor(self):
        ozet = ai.hash_ip("203.0.113.45")
        self.assertNotIn("203.0.113.45", ozet)
        self.assertEqual(len(ozet), 16)
        self.assertNotEqual(ozet, ai.hash_ip("203.0.113.46"))


class ClientIpTests(TestCase):
    """
    IP'yi yanlis basliktan okumak, IP tavanini islevsiz birakir.

    X-Forwarded-For'un ILK girdisi istemci tarafindan uydurulabilir: istemci
    kendi XFF basligini gonderir, vekil gercek IP'yi SONA ekler. Her istekte
    farkli bir sahte ilk girdi gondermek, ilk girdiye bakan bir sayaci
    tamamen etkisiz kilardi.
    """

    def _hash(self, **meta):
        from django.test import RequestFactory

        from core.views import client_ip_hash

        request = RequestFactory().get("/")
        request.META.update(meta)
        return client_ip_hash(request)

    def test_platform_basligi_oncelikli(self):
        beklenen = ai.hash_ip("198.51.100.7")
        self.assertEqual(
            self._hash(
                HTTP_X_VERCEL_FORWARDED_FOR="198.51.100.7",
                HTTP_X_FORWARDED_FOR="1.2.3.4, 198.51.100.7",
            ),
            beklenen,
        )

    def test_uydurma_ilk_girdi_sayaci_kacirmiyor(self):
        """Farkli sahte ilk girdiler ayni ozeti vermeli."""
        a = self._hash(HTTP_X_FORWARDED_FOR="9.9.9.9, 198.51.100.7")
        b = self._hash(HTTP_X_FORWARDED_FOR="7.7.7.7, 198.51.100.7")
        self.assertEqual(a, b)
        self.assertEqual(a, ai.hash_ip("198.51.100.7"))

    def test_baslik_yoksa_remote_addr(self):
        self.assertEqual(
            self._hash(REMOTE_ADDR="192.0.2.9"), ai.hash_ip("192.0.2.9")
        )
