"""
Anonim kimlik katmani.

Sadelik Anayasasi kural 3: "Ilk acilista uyelik YOK. Izin isteme YOK.
Uygulama anonim calisir."

Bu yuzden middleware VERITABANINA YAZMAZ. Sadece cerezde rastgele bir UUID
bulunmasini garanti eder. AnonProfile satiri, ancak kullanici gercekten bir
sey kaydettiginde/yaptiginda (ilk yazma isleminde) olusur. Boylece sadece
ana ekrani acip cikan ziyaretci icin tek bir DB yazmasi bile yapilmaz.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.utils.functional import SimpleLazyObject


def _resolve_profile(request):
    """Cerezdeki UUID icin AnonProfile'i getirir ya da olusturur (tembel)."""
    from core.models import AnonProfile

    profile, _ = AnonProfile.objects.get_or_create(anon_id=request.anon_id)
    return profile


class AnonIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cookie_name = settings.NAPSAM_ANON_COOKIE
        raw = request.COOKIES.get(cookie_name, "")

        try:
            anon_id = uuid.UUID(raw)
            is_new = False
        except (ValueError, AttributeError):
            anon_id = uuid.uuid4()
            is_new = True

        request.anon_id = anon_id
        # request.anon_profile'a DOKUNULMADIKCA sorgu calismaz.
        request.anon_profile = SimpleLazyObject(lambda: _resolve_profile(request))

        response = self.get_response(request)

        if is_new or raw != str(anon_id):
            response.set_cookie(
                cookie_name,
                str(anon_id),
                max_age=settings.NAPSAM_ANON_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=not settings.DEBUG,
            )
        return response
