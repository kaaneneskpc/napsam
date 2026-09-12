"""
Kelime modu (Bolum 7.1-B).

Chip seti gunun saatine gore degisir: gece 03:00'te "kahvalti" gostermek
kullaniciyi bir kez kaybettirir. Etiketler seed havuzundaki `tags` alaniyla
BIREBIR ayni olmak zorunda, yoksa eslesme sifir olur.
"""

from __future__ import annotations

SABAH = ["yürümek", "kahve", "dışarı", "yeni bir şey", "sakin",
         "ellerimi kullanayım", "öğrenmek", "kısa sürsün", "ucuz", "yemek"]

OGLE = ["dışarı", "yürümek", "arkadaşımla", "hareket", "öğrenmek",
        "fotoğraf", "yemek", "kalabalık olmasın", "yeni bir şey", "ucuz"]

AKSAM = ["evde", "sakin", "arkadaşımla", "yemek", "ellerimi kullanayım",
         "kahve", "yeni bir şey", "kısa sürsün", "dışarı", "tek başıma"]

GECE = ["evde", "sakin", "gece", "tek başıma", "kısa sürsün",
        "ellerimi kullanayım", "öğrenmek", "ucuz"]


def for_hour(hour: int) -> list[str]:
    if 6 <= hour < 11:
        return SABAH
    if 11 <= hour < 17:
        return OGLE
    if 17 <= hour < 22:
        return AKSAM
    return GECE


ALL_TAGS = sorted({t for grup in (SABAH, OGLE, AKSAM, GECE) for t in grup})
