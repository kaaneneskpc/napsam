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

    def test_kademe_degistirmek_havuzu_degistiriyor(self):
        """
        Butce dugmesi gorunur bir sey yapmali.

        Eskiden ust kademe alt kademeyi KAPSIYORDU (tavan mantigi); o yuzden
        "Bol" secmek pratikte hicbir sey degistirmiyordu. Artik her kademe
        kendi bandini gosterir.
        """
        az = {s.slug for s in engine.candidates(engine.Context(budget_key="az", now=at(14)))}
        bol = {s.slug for s in engine.candidates(engine.Context(budget_key="bol", now=at(14)))}
        self.assertTrue(az)
        self.assertTrue(bol)
        self.assertFalse(az & bol, "kademeler ayni onerileri gosteriyor")

    # --- saat ve guvenlik ----------------------------------------------
    def test_gece_tek_basina_riskli_oneri_dusuyor(self):
        """Bolum 11 kural 8."""
        ctx = engine.Context(budget_key="bol", now=at(2))
        for item in engine.candidates(ctx):
            self.assertFalse(item.night_unsafe_solo, item.slug)

    def test_gece_yanindaysa_riskli_oneri_kalabilir(self):
        """Kural tek basinayken gecerli; yanindakiler varsa kisit kalkar."""
        yalniz = engine.candidates(engine.Context(budget_key="bedava", now=at(2)))
        yanimda = engine.candidates(
            engine.Context(budget_key="bedava", now=at(2), companions="friends")
        )
        self.assertFalse(any(s.night_unsafe_solo for s in yalniz))
        self.assertTrue(any(s.night_unsafe_solo for s in yanimda))

    def test_gece_kapali_mekanlar_onerilmiyor(self):
        ctx = engine.Context(budget_key="bol", now=at(3))
        for item in engine.candidates(ctx):
            self.assertNotEqual(item.place, "mekanli", item.slug)

    # --- diger sert filtreler -------------------------------------------
    def test_sure_filtresi(self):
        ctx = engine.Context(budget_key="bedava", duration_max=25, now=at(14))
        for item in engine.candidates(ctx):
            self.assertLessEqual(item.duration_min, 25, item.slug)

    def test_kiminle_filtresi(self):
        ctx = engine.Context(budget_key="bedava", companions="kids", now=at(14))
        pool = engine.candidates(ctx)
        self.assertTrue(pool)
        for item in pool:
            self.assertIn("kids", item.companions, item.slug)

    def test_evde_filtresi(self):
        ctx = engine.Context(budget_key="bedava", place_pref="ev", now=at(14))
        for item in engine.candidates(ctx):
            self.assertEqual(item.place, "ev", item.slug)

    def test_yagmurda_acik_hava_onerilmiyor(self):
        ctx = engine.Context(
            budget_key="bedava", now=at(14), weather={"condition": "rain", "tempC": 12}
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


class ConfigCacheTests(TestCase):
    """
    Yavas degisen yapilandirma onbellekten gelmeli.

    Sunucusuz ortamda her sorgu ayri bir gidis-donus: olculen deger
    Turkiye -> Frankfurt icin ~80 ms. Bu testler dort sorgunun bire
    inmesini ve onbellegin yonetim degisikliklerinde dusmesini kilitler.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_sicak_pick_tek_sorgu_atiyor(self):
        engine.pick(engine.Context(budget_key="bedava", now=at(14)))  # isinma
        with self.assertNumQueries(1):
            engine.pick(engine.Context(budget_key="bedava", now=at(14)))

    def test_soguk_pick_yapilandirmayi_bir_kez_okuyor(self):
        with self.assertNumQueries(4):
            engine.pick(engine.Context(budget_key="bedava", now=at(14)))

    def test_butce_degisince_onbellek_dusuyor(self):
        """Bolum 7.2: kod dagitimi gerekmemeli, onbellek bunu geciktirmemeli."""
        from core.models import RemoteConfig

        onceki = engine.budget_ceiling("orta")
        RemoteConfig.objects.filter(key="net_minimum_wage").update(value=56150)
        # update() sinyal tetiklemez; panelden kaydetme yolunu taklit et.
        RemoteConfig.objects.get(key="net_minimum_wage").save()
        self.assertGreater(engine.budget_ceiling("orta"), onceki)

    def test_onbellek_kullaniciya_ozel_veri_tutmuyor(self):
        """Onbellekte yalnizca yapilandirma olmali; kullanici verisi asla."""
        from django.core.cache import cache

        from core import config

        config.budget_tiers(); config.current_wage(); config.active_theme()
        for key in ("napsam:budget_tiers", "napsam:net_minimum_wage", "napsam:weekly_theme"):
            self.assertIsNotNone(cache.get(key), key)


class BudgetIntentTests(TestCase):
    """
    Butce SECIMI bir tercihtir, sadece bir tavan degil.

    Regresyon: kademe yukseltildiginde motor yine bedava oneri donuyordu
    (olcum: "Bol" secildiginde %82 bedava). Kullanici acisindan bu, butce
    dugmesinin hicbir ise yaramamasi demekti.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def test_bedava_kademesinde_hepsi_bedava(self):
        ctx = engine.Context(budget_key="bedava", now=at(14))
        for _ in range(20):
            self.assertEqual(engine.pick(ctx).cost_max, 0)

    def test_ucretli_kademede_bedava_oneri_gelmiyor(self):
        for kademe in ("az", "orta", "iyi", "bol"):
            ctx = engine.Context(budget_key=kademe, now=at(14))
            with self.subTest(kademe=kademe):
                for _ in range(20):
                    self.assertGreater(engine.pick(ctx).cost_max, 0)

    def test_ucretli_kademede_bandin_icinde_kaliniyor(self):
        for kademe in ("orta", "iyi", "bol"):
            lo, hi = engine.budget_band(kademe)
            ctx = engine.Context(budget_key=kademe, now=at(14))
            with self.subTest(kademe=kademe):
                for _ in range(20):
                    secim = engine.pick(ctx)
                    self.assertGreater(secim.cost_max, lo)
                    if hi is not None:
                        self.assertLessEqual(secim.cost_max, hi)

    def test_her_kademede_oneri_var(self):
        """Bos bir kademe, calisan bir motorda bile bozuk deneyim demektir."""
        from core.models import Suggestion

        for kademe in ("bedava", "az", "orta", "iyi", "bol"):
            lo, hi = engine.budget_band(kademe)
            qs = Suggestion.objects.active().seeds()
            n = qs.filter(cost_max=0).count() if kademe == "bedava" else (
                qs.filter(cost_max__gt=lo, cost_max__lte=hi).count()
                if hi is not None else qs.filter(cost_max__gt=lo).count()
            )
            with self.subTest(kademe=kademe):
                self.assertGreater(n, 0, f"{kademe} kademesinde hiç öneri yok")

    def test_tavan_yine_asilmiyor(self):
        """Bant tercihi, Bolum 11 kural 3'u gevsetmemeli."""
        for kademe in ("az", "orta", "iyi"):
            tavan = engine.budget_ceiling(kademe)
            for item in engine.candidates(engine.Context(budget_key=kademe, now=at(14))):
                self.assertLessEqual(item.cost_max, tavan, item.slug)


class KeywordIntentTests(TestCase):
    """
    Secilen kelime SERT filtredir.

    Regresyon: puanlama yumusakti ve secim sabit "ilk 5" arasindan rastgele
    yapiliyordu; eslesen oneri az oldugunda isabet %25'e kadar dusuyordu.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def test_havuzda_yalnizca_eslesenler_var(self):
        for kelime in ("kahve", "yemek", "müzik", "fotoğraf"):
            ctx = engine.Context(budget_key="bol", keywords=[kelime], now=at(14))
            havuz = engine.candidates(ctx)
            with self.subTest(kelime=kelime):
                self.assertTrue(havuz, f"'{kelime}' için hiç öneri yok")
                for item in havuz:
                    self.assertIn(kelime, item.tags or [], item.slug)

    def test_secim_her_zaman_kelimeye_isabet_ediyor(self):
        for kelime in ("kahve", "müzik", "fotoğraf"):
            ctx = engine.Context(budget_key="bol", keywords=[kelime], now=at(14))
            with self.subTest(kelime=kelime):
                for _ in range(20):
                    self.assertIn(kelime, engine.pick(ctx).tags or [])

    def test_birden_fazla_kelimede_en_az_biri_tutuyor(self):
        secilen = ["kahve", "fotoğraf"]
        ctx = engine.Context(budget_key="bol", keywords=secilen, now=at(14))
        for _ in range(20):
            tags = set(engine.pick(ctx).tags or [])
            self.assertTrue(tags & set(secilen))

    def test_eslesme_yoksa_bos_ekran_gosterilmiyor(self):
        """Kelime tutmuyorsa kisit gevser; kullanici bos ekran gormez."""
        ctx = engine.Context(budget_key="bedava", keywords=["hicbiryerde-yok"], now=at(14))
        self.assertIsNotNone(engine.pick(ctx))

    def test_kelime_ve_butce_birlikte_calisiyor(self):
        ctx = engine.Context(budget_key="iyi", keywords=["yemek"], now=at(14))
        lo, hi = engine.budget_band("iyi")
        for _ in range(15):
            secim = engine.pick(ctx)
            self.assertIn("yemek", secim.tags or [])
            self.assertGreater(secim.cost_max, lo)
            self.assertLessEqual(secim.cost_max, hi)


class VarietyTests(TestCase):
    """
    Cesitlilik ve editoryal agirlik dengesi.

    Regresyon: kelime seyrelmesini duzeltmek icin eklenen "en iyiye yakin
    adaylar" kurali, haftalik temayi sert filtre haline getirmisti. 58
    adaylik bir havuzda secimlerin %94'u ayni 6 temali oneriye sikisiyordu.
    Bolum 7.6 temayi "siralamayi agirliklandirir" diye tanimlar.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def test_tema_agirligi_near_score_esigini_asmiyor(self):
        """Tema bonusu esikten buyukse tema filtreye donusur."""
        self.assertLess(engine.W_THEME, engine.NEAR_SCORE)

    def test_havuzun_buyuk_kismi_erisilebilir(self):
        ctx = engine.Context(budget_key="bedava", now=at(14))
        havuz = len(engine.candidates(ctx))
        gorulen = {engine.pick(ctx).slug for _ in range(150)}
        self.assertGreater(
            len(gorulen), havuz * 0.4,
            f"{havuz} adaydan yalnızca {len(gorulen)} tanesi gösteriliyor",
        )

    def test_tema_secimleri_tekeline_almiyor(self):
        tema = engine.active_theme()
        if tema is None:
            self.skipTest("aktif tema yok")
        ctx = engine.Context(budget_key="bedava", now=at(14))
        secimler = [engine.pick(ctx) for _ in range(80)]
        temali = sum(1 for s in secimler if tema.key in (s.theme_tags or []))
        self.assertLess(temali / 80, 0.85, "haftalık tema seçimleri tekeline almış")

    def test_cesitlilik_kelime_isabetini_bozmuyor(self):
        """Genis ornekleme, kullanicinin acik sinyalini seyreltmemeli."""
        for kelime in ("kahve", "müzik", "fotoğraf"):
            ctx = engine.Context(budget_key="bol", keywords=[kelime], now=at(14))
            with self.subTest(kelime=kelime):
                for _ in range(25):
                    self.assertIn(kelime, engine.pick(ctx).tags or [])


class TemplateRenderTests(TestCase):
    """
    Sablon ciktisi denetimleri.

    Regresyon: Django'da {# ... #} yalnizca TEK SATIRDA yorumdur. Cok satirli
    yazilmis bir tanesi yorum sayilmayip duz metin olarak basiliyordu; <head>
    icinde oldugu icin tarayici onu govdeye tasiyip sayfanin tepesine,
    ust barin arkasina yaziyordu.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)

    def test_sayfalarda_islenmemis_sablon_etiketi_yok(self):
        for yol in ("/", "/kaydedilenler/", "/gizlilik/"):
            html = self.client.get(yol).content.decode()
            with self.subTest(yol=yol):
                # {{ ve }} denetlenmez: satir ici JavaScript'te mesru olarak
                # gecerler (ornegin "}})();"). Tehlikeli olanlar sablon yorum
                # ve etiket acilislaridir.
                for kacak in ("{#", "#}", "{%"):
                    self.assertNotIn(kacak, html, f"{yol} içinde işlenmemiş {kacak}")

    def test_filtre_paneli_kapali_basliyor(self):
        """
        Filtre acik dururken kullanici bir sey secmek zorunda hissediyordu.
        Sadelik Anayasasi kural 6: filtreler opsiyoneldir.
        """
        html = self.client.get("/").content.decode()
        self.assertIn('id="refine-panel"', html)
        panel = html[html.index('id="refine-panel"'):][:60]
        self.assertIn("hidden", panel, "filtre paneli kapalı başlamalı")

    def test_birincil_eylem_filtreden_once_geliyor(self):
        """NAPSAM? butonu, isteğe bağlı filtreden ÖNCE görünmeli."""
        html = self.client.get("/").content.decode()
        self.assertLess(
            html.index('id="napsam"'), html.index('id="refine-toggle"'),
            "birincil eylem filtrenin altında kalmış",
        )


class SampleSizeTests(TestCase):
    """
    Ornekleme kapagi havuzla olceklenmeli.

    Regresyon: sabit top_n=15, 58 adaylik havuzda kapagin %26'siydi. Icerik
    138 bedava adaya cikinca oran %11'e dustu; 150 cekiliste yalnizca 39
    farkli oneri gorunuyor, secimlerin %81'i haftalik temali oneriye
    sikisiyordu. Havuz 500'e buyurken ayni hata tekrar etmesin.
    """

    def test_kucuk_havuzda_taban_korunuyor(self):
        for n in (1, 10, 30):
            with self.subTest(havuz=n):
                self.assertEqual(engine.sample_size(n), engine.TOP_N_MIN)

    def test_buyuk_havuzda_kapak_havuzla_buyuyor(self):
        for n in (100, 300, 500):
            with self.subTest(havuz=n):
                self.assertGreaterEqual(engine.sample_size(n), n * engine.TOP_N_FRACTION)
        self.assertGreater(engine.sample_size(500), engine.TOP_N_MIN)

    def test_oran_temayi_yok_etmeyecek_kadar_dar(self):
        """%50'de tema payi %20'ye iniyordu; oran bunun altinda kalmali."""
        self.assertLess(engine.TOP_N_FRACTION, 0.5)
