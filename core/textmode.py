"""
Serbest metin modu (Bolum 7.1-C).

"3 saatim var param yok", "canim deniz gordu", "sevgilimle bir sey yapalim"
gibi dogal cumleleri baglama cevirir.

Bu katman AI DEGILDIR ve AI olmadan da calisir. Amaci, kullanicinin yazdigi
cumleden butce, sure, kiminle, enerji ve ev/disari bilgisini cikarip motora
vermek. Anahtar kelimeler, seed havuzundaki etiket sozlugune eslenir.
AI katmani (Faz 4) bu ayristirmanin USTUNE biner, yerine gecmez.
"""

from __future__ import annotations

import re

# --- butce ----------------------------------------------------------------
BEDAVA_IFADELERI = [
    "param yok", "parasız", "parasiz", "bedava", "ücretsiz", "ucretsiz",
    "beş kuruşum yok", "bes kurusum yok", "bütçem yok", "butcem yok",
    "meteliksiz", "cebim delik", "sıfır bütçe", "harcamak istemiyorum",
]
BOL_IFADELERI = ["param var", "bütçe sorun değil", "butce sorun degil", "fark etmez param"]

# --- kiminle ---------------------------------------------------------------
COMPANION_IFADELERI = {
    "couple": ["sevgilim", "eşim", "esim", "nişanlım", "nisanlim", "partnerim", "ikimiz"],
    "friends": ["arkadaş", "arkadas", "dostum", "kankam", "grupça", "grupca"],
    "family": ["ailem", "ailece", "annem", "babam", "kardeşim", "kardesim", "akraba"],
    "kids": ["çocuk", "cocuk", "çocuğum", "cocugum", "yeğen", "yegen"],
    "solo": ["tek başıma", "tek basima", "yalnızım", "yalnizim", "kendi başıma", "kendi basima"],
}

# --- enerji ----------------------------------------------------------------
DUSUK_ENERJI = [
    "yorgun", "enerjim yok", "halim yok", "bitkin", "uykusuz", "yoruldum",
    "üşengeç", "usengec", "canım hiçbir şey istemiyor", "canim hicbir sey istemiyor",
]
YUKSEK_ENERJI = ["enerjim var", "hareket etmek", "kıpırdamak", "kipirdamak", "koşmak", "kosmak"]

# --- ev / disari -----------------------------------------------------------
EVDE_IFADELERI = ["evde", "evden çıkmak istemiyorum", "evden cikmak istemiyorum", "dışarı çıkamam"]
DISARI_IFADELERI = ["dışarı", "disari", "dışarıda", "disarida", "sokağa", "sokaga", "çıkmak istiyorum"]

# --- etiket sozlugu --------------------------------------------------------
# Kullanicinin yazdigi kelime -> seed havuzundaki etiket
ETIKET_SOZLUGU = {
    "yürü": "yürümek", "yuru": "yürümek", "yürüyüş": "yürümek", "yuruyus": "yürümek",
    "sakin": "sakin", "sessiz": "sakin", "huzur": "sakin", "dingin": "sakin",
    "deniz": "dışarı", "su": "dışarı", "doğa": "dışarı", "doga": "dışarı",
    "kahve": "kahve", "çay": "kahve", "cay": "kahve",
    "yemek": "yemek", "aç": "yemek", "yemek yapmak": "yemek", "pişir": "yemek",
    "öğren": "öğrenmek", "ogren": "öğrenmek", "kurs": "öğrenmek", "merak": "öğrenmek",
    "fotoğraf": "fotoğraf", "fotograf": "fotoğraf", "çek": "fotoğraf",
    "kalabalık istemiyorum": "kalabalık olmasın", "tenha": "kalabalık olmasın",
    "el işi": "ellerimi kullanayım", "elimle": "ellerimi kullanayım",
    "üret": "ellerimi kullanayım", "uret": "ellerimi kullanayım",
    "yeni": "yeni bir şey", "farklı": "yeni bir şey", "farkli": "yeni bir şey",
    "kısa": "kısa sürsün", "kisa": "kısa sürsün", "çabuk": "kısa sürsün",
    "gece": "gece", "akşam": "gece", "aksam": "gece",
    "spor": "hareket", "hareket": "hareket", "egzersiz": "hareket",
    "ucuz": "ucuz", "bedava": "ucuz",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_duration(text: str) -> int | None:
    """Metinden kullanilabilir sureyi dakika olarak cikarir."""
    if "yarım saat" in text or "yarim saat" in text:
        return 30
    if "bütün gün" in text or "butun gun" in text or "tüm gün" in text:
        return 480

    if m := re.search(r"(\d+)\s*saat", text):
        return int(m.group(1)) * 60
    if m := re.search(r"(\d+)\s*(dakika|dk)", text):
        return int(m.group(1))
    return None


def parse_budget(text: str, tiers: list) -> str | None:
    """
    Metinden butce kademesini cikarir.

    Once acik ifadeler ("param yok"), sonra gecen TL tutari denenir.
    `tiers`, BudgetTier nesnelerinin siraliListesidir.
    """
    for ifade in BEDAVA_IFADELERI:
        if ifade in text:
            return "bedava"
    for ifade in BOL_IFADELERI:
        if ifade in text:
            return "bol"

    if m := re.search(r"(\d+)\s*(tl|lira|₺)", text):
        amount = int(m.group(1))
        from core import config

        wage = config.current_wage()
        for tier in tiers:
            top = tier.amount_max(wage)
            if top is None or amount <= top:
                return tier.key
    return None


def parse_companions(text: str) -> str | None:
    for key, ifadeler in COMPANION_IFADELERI.items():
        if any(i in text for i in ifadeler):
            return key
    return None


def parse_energy(text: str) -> str | None:
    if any(i in text for i in DUSUK_ENERJI):
        return "low"
    if any(i in text for i in YUKSEK_ENERJI):
        return "high"
    return None


def parse_place(text: str) -> str | None:
    if any(i in text for i in EVDE_IFADELERI):
        return "ev"
    if any(i in text for i in DISARI_IFADELERI):
        return "disari"
    return None


def parse_keywords(text: str) -> list[str]:
    bulunan: list[str] = []
    for anahtar, etiket in ETIKET_SOZLUGU.items():
        if anahtar in text and etiket not in bulunan:
            bulunan.append(etiket)
    return bulunan


def parse(text: str) -> dict:
    """
    Serbest metni motor baglamina cevirir.

    Donen sozlukte yalnizca METINDEN GERCEKTEN CIKARILABILEN alanlar bulunur;
    tahmin edilemeyen alanlar hic konmaz ki motor varsayilanlarini kullansin.
    """
    from core import config

    normalized = _normalize(text)
    if not normalized:
        return {}

    tiers = config.budget_tiers()

    result: dict = {}
    if (budget := parse_budget(normalized, tiers)) is not None:
        result["budget_key"] = budget
    if (duration := parse_duration(normalized)) is not None:
        result["duration_max"] = duration
    if (companions := parse_companions(normalized)) is not None:
        result["companions"] = companions
    if (energy := parse_energy(normalized)) is not None:
        result["energy"] = energy
    if (place := parse_place(normalized)) is not None:
        result["place_pref"] = place
    if keywords := parse_keywords(normalized):
        result["keywords"] = keywords
    return result
