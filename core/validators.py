"""
Icerik denetleyicileri.

Bolum 4 ve Bolum 11'in en onemli kurali: bir oneri Turkiye'nin HER YERINDE
yapilabilir olmali. Sehir, ilce, semt, cadde ya da isletme ismi geciyorsa
oneri havuza GIRMEZ.

Bu modul iki yerde kullanilir:
  1. seed komutu    - elle yazilan icerigi ice aktarirken
  2. AI son-denetim - modelin urettigi ciktida (Faz 4)
"""

from __future__ import annotations

import re
import unicodedata

# Turkiye'nin 81 ili. Bir oneride il adi geciyorsa konum bagimsiz degildir.
IL_ADLARI = {
    "adana", "adıyaman", "afyonkarahisar", "ağrı", "aksaray", "amasya", "ankara",
    "antalya", "ardahan", "artvin", "aydın", "balıkesir", "bartın", "batman",
    "bayburt", "bilecik", "bingöl", "bitlis", "bolu", "burdur", "bursa",
    "çanakkale", "çankırı", "çorum", "denizli", "diyarbakır", "düzce", "edirne",
    "elazığ", "erzincan", "erzurum", "eskişehir", "gaziantep", "giresun",
    "gümüşhane", "hakkari", "hatay", "ığdır", "ısparta", "istanbul", "izmir",
    "kahramanmaraş", "karabük", "karaman", "kars", "kastamonu", "kayseri",
    "kilis", "kırıkkale", "kırklareli", "kırşehir", "kocaeli", "konya",
    "kütahya", "malatya", "manisa", "mardin", "mersin", "muğla", "muş",
    "nevşehir", "niğde", "ordu", "osmaniye", "rize", "sakarya", "samsun",
    "şanlıurfa", "siirt", "sinop", "sivas", "şırnak", "tekirdağ", "tokat",
    "trabzon", "tunceli", "uşak", "van", "yalova", "yozgat", "zonguldak",
}

# Sik gecen semt/ilce ve yalnizca belirli sehirlerde anlamli olan terimler.
YASAKLI_YER_TERIMLERI = {
    "kadıköy", "beşiktaş", "üsküdar", "beyoğlu", "taksim", "moda", "bebek",
    "ortaköy", "eminönü", "karaköy", "nişantaşı", "bağdat caddeleri",
    "kızılay", "tunalı", "çankaya", "alsancak", "konak", "karşıyaka",
    "boğaz", "boğaziçi", "haliç", "adalar", "büyükada",
    # Yalnizca deniz/vapur olan sehirlerde yapilabilir olanlar:
    "vapur", "vapura", "vapurla", "iskele", "sahil yolu",
    # Metro/tramvay her ilde yok; "toplu tasima" demek dogru olani.
    "metrobüs", "marmaray", "teleferik",
}

# Bolum 11 kural 9: yasakli icerik.
YASAKLI_ICERIK = {
    "bahis", "kumar", "iddaa", "casino", "rulet",
    "rakı", "bira", "şarap", "votka", "viski", "alkol",
    "sigara", "nargile",
}

# Bolum 8.3: koc dili ve Ingilizce devsirme yasak.
YASAKLI_TON = {
    "hayatını değiştir", "yolculuğa çık", "potansiyelini keşfet",
    "mindful", "self-care", "detoks", "motivasyon", "enerjini yükselt",
}


class ContentError(ValueError):
    """Bir oneri icerik kurallarindan birini ciğnediginde firlatilir."""


# Turkce kucultme, blok listesi eslestirmesinde iki sessiz tuzak barindirir:
#   "İstanbul".lower() -> "i" + U+0307 (birlesik nokta), "istanbul" ile eslesmez
#   "Isparta".lower()  -> "isparta"  ama dogru kucuk hali "ısparta"
# Ikisi de kacak demektir; kacak, urunun en onemli kuralini deler.
# Bu yuzden karsilastirma ASCII'ye katlanarak yapilir: fazla eslesmek
# (yanlis pozitif) burada guvenli taraftir, kacirmak degildir.
_KATLAMA = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "İ": "i", "ö": "o", "ş": "s", "ü": "u",
    "Ç": "c", "Ğ": "g", "I": "i", "Ö": "o", "Ş": "s", "Ü": "u", "â": "a", "î": "i", "û": "u",
})


def fold(metin: str) -> str:
    """Metni buyuk/kucuk ve Turkce karakter farklarindan arindirir."""
    metin = metin.translate(_KATLAMA).lower()
    # Kucultmeden arta kalan birlesik isaretleri (ornegin U+0307) at.
    metin = unicodedata.normalize("NFD", metin)
    return "".join(ch for ch in metin if not unicodedata.combining(ch))


def _kelimeler(metin: str) -> set[str]:
    return set(re.findall(r"[\w]+", fold(metin)))


# Blok listeleri de ayni bicimde katlanir ki iki taraf ayni dilde konussun.
_IL_ADLARI_F = {fold(x) for x in IL_ADLARI}

# Tek kelimelik terimler TAM KELIME olarak aranir; alt-dize aramasi
# "konaklama" icinde "konak", "bebeginle" icinde "bebek" bulup yanlis alarm
# veriyordu. Cok kelimeli terimler ise ifade olduklari icin alt-dize kalir.
_YASAKLI_YER_KELIME_F = {fold(x) for x in YASAKLI_YER_TERIMLERI if " " not in x}
_YASAKLI_YER_IFADE_F = {fold(x) for x in YASAKLI_YER_TERIMLERI if " " in x}
_YASAKLI_ICERIK_F = {fold(x) for x in YASAKLI_ICERIK}
_YASAKLI_TON_F = {fold(x) for x in YASAKLI_TON}


def suggestion_text(data: dict) -> str:
    """Bir onerinin denetlenecek tum serbest metnini birlestirir."""
    parcalar = [
        data.get("title", ""),
        data.get("hook", ""),
        data.get("fallback", ""),
        data.get("venue_type", ""),
        " ".join(data.get("steps", []) or []),
    ]
    return " ".join(str(p) for p in parcalar)


def check_location_independence(data: dict) -> list[str]:
    """Sehir/ilce/isletme ismi kacaklarini bulur."""
    metin = suggestion_text(data)
    kelimeler = _kelimeler(metin)
    katlanmis = fold(metin)

    hatalar = []
    for il in sorted(_IL_ADLARI_F & kelimeler):
        hatalar.append(f"il adı geçiyor: '{il}'")
    for terim in sorted(_YASAKLI_YER_KELIME_F & kelimeler):
        hatalar.append(f"yere bağımlı terim: '{terim}'")
    for ifade in sorted(_YASAKLI_YER_IFADE_F):
        if ifade in katlanmis:
            hatalar.append(f"yere bağımlı ifade: '{ifade}'")
    return hatalar


def check_safety(data: dict) -> list[str]:
    kelimeler = _kelimeler(suggestion_text(data))
    return [
        f"yasaklı içerik: '{terim}'" for terim in sorted(_YASAKLI_ICERIK_F & kelimeler)
    ]


def check_tone(data: dict) -> list[str]:
    katlanmis = fold(suggestion_text(data))
    return [f"yasaklı ton: '{t}'" for t in sorted(_YASAKLI_TON_F) if t in katlanmis]


def check_shape(data: dict) -> list[str]:
    """Bolum 3 kural 8 + Bolum 9 sekil kurallari."""
    hatalar = []

    baslik = str(data.get("title", "")).strip()
    if not baslik:
        hatalar.append("başlık boş")
    elif len(baslik.split()) > 6:
        hatalar.append(f"başlık 6 kelimeyi aşıyor ({len(baslik.split())})")

    steps = data.get("steps") or []
    if not 2 <= len(steps) <= 3:
        hatalar.append(f"adım sayısı 2-3 olmalı ({len(steps)})")
    if any(not str(s).strip() for s in steps):
        hatalar.append("boş adım var")

    if not str(data.get("fallback", "")).strip():
        hatalar.append("fallback boş — zorunlu")

    hook = str(data.get("hook", "")).strip()
    if not hook:
        hatalar.append("hook boş")
    elif hook.count(".") + hook.count("?") + hook.count("!") > 1:
        hatalar.append("hook tek cümle olmalı")

    if data.get("place") == "mekanli" and not str(data.get("venue_type", "")).strip():
        hatalar.append("place=mekanli ama venue_type boş")

    if data.get("place") != "mekanli" and str(data.get("venue_type", "")).strip():
        hatalar.append("venue_type sadece place=mekanli için doldurulur")

    if len(data.get("required_items") or []) > 3:
        hatalar.append("required_items en fazla 3 madde")

    # seasonality AY NUMARASI listesidir. Buraya "ramazan" gibi bir etiket
    # yazmak oneriyi sessizce olduruyordu: ay filtresi hicbir zaman tutmaz
    # ve oneri havuzda gorunur olmasina ragmen ASLA gosterilmez.
    for ay in data.get("seasonality") or []:
        if not isinstance(ay, int) or not 1 <= ay <= 12:
            hatalar.append(f"seasonality ay numarası olmalı (1-12), bulunan: {ay!r}")

    if (data.get("cost_max") or 0) < (data.get("cost_min") or 0):
        hatalar.append("cost_max < cost_min")

    if (data.get("duration_max") or 0) < (data.get("duration_min") or 0):
        hatalar.append("duration_max < duration_min")

    return hatalar


def validate(data: dict, *, strict: bool = True) -> list[str]:
    """Tum denetimleri calistirir. strict=True ise hata varsa firlatir."""
    hatalar = (
        check_shape(data)
        + check_location_independence(data)
        + check_safety(data)
        + check_tone(data)
    )
    if hatalar and strict:
        raise ContentError(f"{data.get('slug', '?')}: " + "; ".join(hatalar))
    return hatalar
