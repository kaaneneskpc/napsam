"""
Gorunumler.

Toplam 3 sayfa var (Bolum 12 dort diyor; "Ayarlar" web'de ayri bir ekrana
gerek duymadigi icin alt bilgiye indi):
    1. Ana ekran      - konsol + hazir oneri karti
    2. Kaydedilenler  - duz liste
    3. Gizlilik       - KVKK metni (Bolum 15)

Ana ekran ACILIR ACILMAZ bir oneriyle gelir. Dokumandaki "acilistan oneriye
1 dokunus" hedefi boylece 0 dokunusa iner; NAPSAM? butonu yenileme icindir.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core import ai, chips, config, engine, textmode
from core.models import AnonProfile, BudgetTier, StateStatus, Suggestion, SuggestionState
from core.serializers import suggestion_to_dict

VALID_ENERGY = {"low", "medium", "high"}
VALID_PLACE_PREF = {"ev", "disari"}
VALID_COMPANIONS = {"solo", "couple", "friends", "family", "kids"}
MAX_EXCLUDED = 40
MAX_KEYWORDS = 8
MAX_TEXT_LEN = 280


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------
def existing_profile(request):
    """
    Varsa profili getirir, YOKSA OLUSTURMAZ.

    Sadece okuma yapan yollarda kullanilir; boylece ana ekrani acip cikan
    ziyaretci icin tek bir veritabani yazmasi bile yapilmaz.

    Cerez bu istekte uretildiyse profil arama sorgusu da atilmaz: yeni bir
    kimlige ait kayit tanim geregi yoktur.
    """
    if getattr(request, "anon_is_new", False):
        return None
    return AnonProfile.objects.filter(anon_id=request.anon_id).first()


def budget_tier_payload() -> list[dict]:
    wage = config.current_wage()
    return [
        {
            "key": t.key,
            "label": t.label,
            "hint": t.hint,
            "amount": t.describe(wage),
            "max": t.amount_max(wage),
        }
        for t in config.budget_tiers()
    ]


def client_ip_hash(request) -> str | None:
    """
    Istemci IP'sinin ozeti (acik IP saklanmaz).

    DIKKAT: X-Forwarded-For'un ILK girdisi guvenilir DEGILDIR; istemci kendi
    basligini gonderebilir, vekil gercek IP'yi sona ekler. Bu yuzden once
    platformun kendi yazdigi basliklara bakilir, en son care olarak XFF'in
    SON girdisi kullanilir.
    """
    for header in ("HTTP_X_VERCEL_FORWARDED_FOR", "HTTP_X_REAL_IP"):
        if value := request.META.get(header, "").strip():
            return ai.hash_ip(value.split(",")[-1].strip())

    if xff := request.META.get("HTTP_X_FORWARDED_FOR", "").strip():
        return ai.hash_ip(xff.split(",")[-1].strip())

    if remote := request.META.get("REMOTE_ADDR", "").strip():
        return ai.hash_ip(remote)
    return None


def _parse_body(request) -> dict:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Geçersiz JSON")
    if not isinstance(data, dict):
        raise ValueError("Gövde bir nesne olmalı")
    return data


def build_context(data: dict) -> tuple[engine.Context, str]:
    """Istek govdesinden motor baglamini kurar. Sehir/konum ALANI YOKTUR."""
    mode = str(data.get("mode", "quick"))[:16]

    kwargs: dict = {
        "budget_key": str(data.get("budget", "bedava"))[:32],
        "now": timezone.localtime(),
    }

    keywords = data.get("keywords") or []
    if isinstance(keywords, list):
        kwargs["keywords"] = [str(k)[:40] for k in keywords[:MAX_KEYWORDS]]

    filters = data.get("filters") or {}
    if isinstance(filters, dict):
        if (d := filters.get("duration")) and str(d).isdigit():
            kwargs["duration_max"] = min(int(d), 1440)
        if (c := filters.get("companions")) in VALID_COMPANIONS:
            kwargs["companions"] = c
        if (e := filters.get("energy")) in VALID_ENERGY:
            kwargs["energy"] = e
        if (p := filters.get("place")) in VALID_PLACE_PREF:
            kwargs["place_pref"] = p

    # Serbest metin, acikca verilmemis alanlari doldurur; kullanicinin
    # elle sectigi degerleri EZMEZ.
    if mode == "text" and (raw := str(data.get("text", ""))[:MAX_TEXT_LEN].strip()):
        for key, value in textmode.parse(raw).items():
            if key == "keywords":
                kwargs.setdefault("keywords", [])
                kwargs["keywords"] = list(dict.fromkeys(kwargs["keywords"] + value))
            elif key == "budget_key":
                # Metinde acik butce ifadesi varsa onu dikkate al.
                kwargs["budget_key"] = value
            else:
                kwargs.setdefault(key, value)

    excluded = data.get("excluded") or []
    if isinstance(excluded, list):
        kwargs["excluded"] = {str(s)[:80] for s in excluded[:MAX_EXCLUDED]}

    weather = data.get("weather")
    if isinstance(weather, dict):
        kwargs["weather"] = weather

    return engine.Context(**kwargs), mode


# --------------------------------------------------------------------------
# Sayfalar
# --------------------------------------------------------------------------
@ensure_csrf_cookie
@require_GET
def home(request):
    profile = existing_profile(request)
    now = timezone.localtime()

    ctx = engine.Context(budget_key="bedava", now=now)
    suggestion = engine.pick(ctx, profile)

    saved_slugs: list[str] = []
    if profile is not None:
        saved_slugs = list(
            SuggestionState.objects.filter(profile=profile, status=StateStatus.SAVED)
            .values_list("suggestion__slug", flat=True)
        )

    return render(request, "index.html", {
        "suggestion": suggestion,
        "suggestion_json": json.dumps(
            suggestion_to_dict(suggestion) if suggestion else None, ensure_ascii=False
        ),
        "budget_tiers": budget_tier_payload(),
        "chips": chips.for_hour(now.hour),
        "saved_slugs_json": json.dumps(saved_slugs),
        "saved_count": len(saved_slugs),
    })


@require_GET
def saved(request):
    profile = existing_profile(request)
    states = []
    if profile is not None:
        states = list(
            SuggestionState.objects.filter(profile=profile, status=StateStatus.SAVED)
            .select_related("suggestion")
            .order_by("-saved_at", "-updated_at")
        )
    return render(request, "saved.html", {
        "states": states,
        "saved_count": len(states),
    })


@require_GET
def privacy(request):
    return render(request, "privacy.html", {"saved_count": 0})


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@require_POST
def api_suggest(request):
    try:
        data = _parse_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    ctx, mode = build_context(data)
    profile = existing_profile(request)

    suggestion = None

    # AI katmani YALNIZCA serbest metin modunda denenir (Bolum 10).
    # Basarisiz olursa kullaniciya hata gosterilmez, seed havuzuna dusulur.
    if mode == "text" and ai.is_enabled():
        raw_text = str(data.get("text", ""))[:MAX_TEXT_LEN].strip()
        if raw_text:
            suggestion = ai.generate(
                ctx, raw_text, request.anon_id,
                engine.budget_ceiling(ctx.budget_key),
                ip_hash=client_ip_hash(request),
            )

    if suggestion is None:
        suggestion = engine.pick(ctx, profile)

    if suggestion is None:
        return JsonResponse({"suggestion": None}, status=200)

    return JsonResponse({
        "suggestion": suggestion_to_dict(suggestion),
        "meta": {
            "mode": mode,
            "budget": ctx.budget_key,
            "keywords": ctx.keywords,
            "isNight": ctx.is_night,
            "aiQuotaLeft": (
                ai.quota_left(request.anon_id, client_ip_hash(request))
                if ai.is_enabled() else None
            ),
        },
    })


@require_POST
def api_state(request):
    """
    Kaydet / kaldir / gec / yaptim.

    Bu uc eylem, uygulamanin veritabanina YAZDIGI TEK yerdir. Profil satiri
    da ilk kez burada olusur.
    """
    try:
        data = _parse_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    slug = str(data.get("slug", ""))[:80]
    action = str(data.get("action", ""))[:16]

    suggestion = Suggestion.objects.filter(slug=slug, is_active=True).first()
    if suggestion is None:
        return JsonResponse({"error": "Öneri bulunamadı"}, status=404)

    profile = request.anon_profile  # burada olusur
    now = timezone.now()

    if action == "save":
        SuggestionState.objects.update_or_create(
            profile=profile, suggestion=suggestion,
            defaults={"status": StateStatus.SAVED, "saved_at": now, "dismissed_until": None},
        )
    elif action == "unsave":
        SuggestionState.objects.filter(profile=profile, suggestion=suggestion).update(
            status=StateStatus.SHOWN, saved_at=None
        )
    elif action == "dismiss":
        engine.mark_dismissed(profile, suggestion)
    elif action == "done":
        SuggestionState.objects.update_or_create(
            profile=profile, suggestion=suggestion,
            defaults={"status": StateStatus.DONE, "done_at": now},
        )
    else:
        return HttpResponseBadRequest("Geçersiz eylem")

    saved_count = SuggestionState.objects.filter(
        profile=profile, status=StateStatus.SAVED
    ).count()
    return JsonResponse({"ok": True, "action": action, "savedCount": saved_count})


@require_GET
def healthz(request):
    return JsonResponse({
        "ok": True,
        "suggestions": Suggestion.objects.active().count(),
        "wage": str(engine.current_wage()),
    })
