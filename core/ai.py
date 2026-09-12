"""
AI katmani - Katman 2 (Bolum 10 + Bolum 11).

Ne zaman devreye girer:
  - YALNIZCA serbest metin ("Yaz") modunda,
  - GEMINI_API_KEY tanimliysa,
  - gunluk kota asilmadiysa,
  - ve onbellekte ayni baglam yoksa.

Bunlardan biri saglanmazsa istek sessizce seed havuzuna duser. AI katmani
bir SUS, cekirdek degil; anahtar olmadan da uygulama eksiksiz calisir.

Guvenlik:
  - Anahtar yalnizca sunucuda. Istemciye hicbir kosulda gitmez.
  - Model ciktisi, seed icerigiyle AYNI denetleyicilerden gecer
    (core/validators.py). Konum sizan, yasakli icerik tasiyan ya da butceyi
    asan cikti REDDEDILIR ve seed havuzuna dusulur (Bolum 15).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core import validators
from core.models import (
    AiRequestCache,
    AiUsage,
    Category,
    Energy,
    Place,
    Suggestion,
    SuggestionSource,
)

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TIMEOUT_SECONDS = 12

# Bolum 11'deki sistem promptu. Metni degistirirken kural numaralarini koru;
# her madde bir urun kararinin karsiligidir.
SYSTEM_PROMPT = """\
Sen Türkiye'de yaşayan insanlara gündelik aktivite öneren bir asistansın.
Görevin: verilen bağlama uygun, SOMUT ve HEMEN YAPILABİLİR tek bir öneri üretmek.

MUTLAK KURALLAR:

1. KONUM BAĞIMSIZLIĞI — EN ÖNEMLİ KURAL
   Öneri Türkiye'nin HER YERİNDE yapılabilir olmalı. Şehir, ilçe, semt,
   mahalle, cadde, plaj, meydan veya işletme ismi ASLA yazma.
   YANLIŞ: "Kadıköy sahilinde yürü"  DOĞRU: "En yakın su kenarına yürü"
   YANLIŞ: "Vapura bin"              DOĞRU: "Toplu taşımada son durağa kadar git"
   Sadece büyük şehirde işe yarayan öneri ÜRETME.
   Önerinin özü EYLEM olsun, YER değil.

2. GERÇEK MEKAN İSMİ UYDURMA. place='mekanli' ise sadece venueType yaz
   ("sinema", "kütüphane", "halı saha"). İşletme ismi verme.

3. BÜTÇEYİ ASLA AŞMA. costMax kullanıcı bütçesini geçemez. Bütçe 0 ise
   gerçekten 0 TL'ye yapılabilir olsun — ulaşım maliyetini de düşün.

4. SOMUT OL. "Dışarı çık", "yeni bir şey dene", "hobinle ilgilen" YASAK.
   Öneri bugün, şu an, ek hazırlık olmadan başlanabilir olmalı.

5. steps ve fallback boş geçilemez. Kullanıcı kartı kapattığında ne
   yapacağını bilmiyorsa öneri başarısızdır.

6. KISA YAZ. title en fazla 6 kelime ve EMİR KİPİNDE olsun
   ("En yakın su kenarına yürü" gibi; "Su Kenarı Yürüyüşü" gibi DEĞİL).
   hook TEK cümle. steps en fazla 3 madde, her madde tek satır.
   venueType yalnızca place="mekanli" iken doldurulur, aksi halde boş bırakılır.
   Uzun metin bu üründe hatadır.

7. Saat ve mevsimle çelişme. Gece 23:00'te piknik, ağustos öğlesinde koşu önerme.

8. GÜVENLİK: Gece geç saatte tek başına ıssız/tenha yer önerme.

9. YASAK: kumar/bahis, alkol ve madde teşviki, tehlikeli aktivite, yasa dışı
   her şey, sağlık tavsiyesi, finansal tavsiye, siyasi/kalabalık etkinlik.

10. KÜLTÜREL DUYARLILIK: Ramazan'da gündüz yeme-içme odaklı öneri verme.
    Dini pratikler hakkında yorum yapma; takvimi sadece bağlam olarak kullan.

11. TON: Sen dili, kısa cümle. Koçvari dil YASAK ("Hayatını değiştir!").
    İngilizce devşirme YASAK ("mindful", "self-care", "detoks").

DEĞERLENDİRME SIRASI:
bütçe > saat > süre > kiminle > enerji > ev/dışarı > mevsim/takvim > kelimeler
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "fallback": {"type": "string"},
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "costMin": {"type": "integer"},
        "costMax": {"type": "integer"},
        "costNote": {"type": "string"},
        "durationMin": {"type": "integer"},
        "durationMax": {"type": "integer"},
        "energy": {"type": "string", "enum": [e.value for e in Energy]},
        "place": {"type": "string", "enum": [p.value for p in Place]},
        "venueType": {"type": "string"},
        "requiredItems": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title", "hook", "steps", "fallback", "category",
        "costMin", "costMax", "durationMin", "durationMax",
        "energy", "place",
    ],
}


# --------------------------------------------------------------------------
# Durum
# --------------------------------------------------------------------------
def is_enabled() -> bool:
    return bool(settings.GEMINI_API_KEY)


def signature(ctx, text: str) -> str:
    """Ayni baglam icin ayni imza. Saat, saat diliminde yuvarlanir."""
    payload = json.dumps({
        "text": text.strip().lower(),
        "budget": ctx.budget_key,
        "keywords": sorted(ctx.keywords),
        "duration": ctx.duration_max,
        "companions": ctx.companions,
        "energy": ctx.energy,
        "place": ctx.place_pref,
        "hour": ctx.now.hour,
        "month": ctx.now.month,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def quota_left(anon_id) -> int:
    today = timezone.localdate()
    usage = AiUsage.objects.filter(anon_id=anon_id, day=today).first()
    used = usage.count if usage else 0
    return max(settings.NAPSAM_AI_DAILY_LIMIT - used, 0)


def _consume_quota(anon_id) -> None:
    today = timezone.localdate()
    usage, _ = AiUsage.objects.get_or_create(anon_id=anon_id, day=today)
    usage.count += 1
    usage.save(update_fields=["count"])


# --------------------------------------------------------------------------
# Model cagrisi
# --------------------------------------------------------------------------
def _build_input(ctx, text: str, budget_ceiling: int | None) -> str:
    context = {
        "userText": text,
        "keywords": ctx.keywords,
        "budget": {
            "tier": ctx.budget_key,
            "maxTRY": budget_ceiling,
            "currency": "TRY",
        },
        "timeOfDay": ctx.now.strftime("%H:%M"),
        "dayType": "weekend" if ctx.now.weekday() >= 5 else "weekday",
        "durationMinutes": ctx.duration_max,
        "companions": ctx.companions or "unknown",
        "energy": ctx.energy or "unknown",
        "placePreference": ctx.place_pref or "unknown",
        "month": ctx.now.month,
        "isNight": ctx.is_night,
    }
    # Dikkat: baglamda sehir/ilce alani YOK ve olmayacak.
    return (
        f"{SYSTEM_PROMPT}\n\nKULLANICI BAĞLAMI:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Bu bağlama uygun TEK bir öneri üret."
    )


def _extract_text(payload: dict) -> str:
    """
    Interactions API yanitindan metni cikarir.

    Yanit sekli surumler arasinda degistigi icin savunmaci okunur:
    once kisayol alanlar, sonra steps icindeki metin bloklari.
    """
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    interaction = payload.get("interaction")
    if isinstance(interaction, dict) and isinstance(interaction.get("output_text"), str):
        return interaction["output_text"]

    for source in (payload, interaction or {}):
        for step in reversed(source.get("steps") or []):
            for block in step.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    return block["text"]
    return ""


def _call_model(prompt: str) -> dict | None:
    try:
        response = requests.post(
            API_URL,
            headers={
                "x-goog-api-key": settings.GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": settings.GEMINI_MODEL,
                "input": prompt,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": RESPONSE_SCHEMA,
                },
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Gemini isteği başarısız: %s", exc)
        return None

    if response.status_code != 200:
        logger.warning("Gemini %s: %s", response.status_code, response.text[:300])
        return None

    raw = _extract_text(response.json())
    if not raw:
        logger.warning("Gemini yanıtında metin bulunamadı")
        return None

    raw = raw.strip()
    if raw.startswith("```"):                     # kod bloguna sarilmis olabilir
        raw = raw.split("```")[1].removeprefix("json").strip()

    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Gemini çıktısı JSON değil: %s", raw[:200])
        return None

    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# Son denetim ve kayit
# --------------------------------------------------------------------------
def _to_suggestion_fields(data: dict, ctx) -> dict:
    """
    Model ciktisini model alanlarina cevirir ve KUCUK tutarsizliklari duzeltir.

    Amac: "venueType dolu ama place=yakin" gibi zararsiz bir hata yuzunden
    iyi bir oneriyi cope atmamak. Duzeltilemeyen ihlaller (konum sizmasi,
    yasakli icerik, butce asimi) _post_check'te reddedilir.
    """
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()][:3]

    place = data.get("place") or Place.EV
    venue = str(data.get("venueType", "")).strip()[:40]
    if place != Place.MEKANLI:
        venue = ""                      # mekan turu yalnizca mekanli icin anlamli
    elif not venue:
        place = Place.YAKIN             # mekan turu yoksa "mekanli" iddiasi gecersiz

    cost_min = max(int(data.get("costMin", 0) or 0), 0)
    cost_max = max(int(data.get("costMax", 0) or 0), cost_min)
    duration_min = max(int(data.get("durationMin", 30) or 30), 1)
    duration_max = max(int(data.get("durationMax", 60) or 60), duration_min)

    return {
        "title": str(data.get("title", "")).strip()[:60],
        "hook": str(data.get("hook", "")).strip()[:160],
        "steps": steps,
        "fallback": str(data.get("fallback", "")).strip(),
        "category": data.get("category") or Category.EV_ICI,
        "cost_min": cost_min,
        "cost_max": cost_max,
        "cost_note": str(data.get("costNote", "")).strip()[:80],
        "duration_min": duration_min,
        "duration_max": duration_max,
        "energy": data.get("energy") or Energy.MEDIUM,
        "place": place,
        "venue_type": venue,
        "required_items": [str(i)[:40] for i in (data.get("requiredItems") or [])][:3],
        "companions": [ctx.companions] if ctx.companions else ["solo", "couple", "friends"],
        "weather_need": "any",
        "seasonality": [],
        "cultural_tags": [],
        "theme_tags": [],
        "tags": list(ctx.keywords),
        "night_unsafe_solo": False,
        "source": SuggestionSource.AI,
        "is_active": True,
    }


def _post_check(fields: dict, budget_ceiling: int | None) -> list[str]:
    """Seed icerigiyle AYNI kurallar. Gecemeyen cikti kullanilmaz."""
    payload = {
        "slug": "ai", "title": fields["title"], "hook": fields["hook"],
        "steps": fields["steps"], "fallback": fields["fallback"],
        "place": fields["place"], "venue_type": fields["venue_type"],
        "cost_min": fields["cost_min"], "cost_max": fields["cost_max"],
        "duration_min": fields["duration_min"], "duration_max": fields["duration_max"],
        "required_items": fields["required_items"],
    }
    problems = validators.validate(payload, strict=False)

    if budget_ceiling is not None and fields["cost_max"] > budget_ceiling:
        problems.append(
            f"bütçe aşıldı: {fields['cost_max']} > {budget_ceiling}"
        )
    return problems


def _unique_slug(title: str) -> str:
    base = slugify(title)[:50] or "oneri"
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"ai-{base}-{digest}"[:80]


# --------------------------------------------------------------------------
# Genel arayuz
# --------------------------------------------------------------------------
def generate(ctx, text: str, anon_id, budget_ceiling: int | None) -> Suggestion | None:
    """
    Serbest metin icin AI onerisi uretir.

    Basarisiz olan her yolda None doner; cagiran taraf seed havuzuna duser.
    Kullaniciya asla hata gosterilmez, sadece daha az ozel bir oneri gelir.
    """
    if not is_enabled() or not text.strip():
        return None

    sig = signature(ctx, text)

    # 1) Onbellek (7 gun TTL)
    fresh_after = timezone.now() - timedelta(days=settings.NAPSAM_AI_CACHE_TTL_DAYS)
    cached = (
        AiRequestCache.objects.filter(signature=sig, created_at__gte=fresh_after)
        .select_related("suggestion")
        .first()
    )
    if cached:
        return cached.suggestion

    # 2) Kota
    if quota_left(anon_id) <= 0:
        logger.info("AI günlük kota doldu, seed havuzuna düşülüyor")
        return None

    # 3) Cagri
    _consume_quota(anon_id)
    data = _call_model(_build_input(ctx, text, budget_ceiling))
    if data is None:
        return None

    # 4) Son denetim
    fields = _to_suggestion_fields(data, ctx)
    problems = _post_check(fields, budget_ceiling)
    if problems:
        logger.warning("AI çıktısı reddedildi: %s", "; ".join(problems))
        return None

    # 5) Kayit + onbellek
    slug = _unique_slug(fields["title"])
    with transaction.atomic():
        suggestion, _ = Suggestion.objects.update_or_create(slug=slug, defaults=fields)
        AiRequestCache.objects.update_or_create(
            signature=sig, defaults={"suggestion": suggestion}
        )
    return suggestion
