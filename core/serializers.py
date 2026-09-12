"""Oneri -> JSON donusumu. Tek yerde tutuldu ki kart her yerde ayni gorunsun."""

from __future__ import annotations

from core.models import Suggestion


def suggestion_to_dict(s: Suggestion) -> dict:
    return {
        "slug": s.slug,
        "title": s.title,
        "hook": s.hook,
        "steps": list(s.steps or []),
        "fallback": s.fallback,
        "category": s.category,
        "categoryLabel": s.get_category_display(),
        "badges": {
            "cost": s.cost_badge,
            "duration": s.duration_badge,
            "place": s.place_badge,
            "companion": s.companion_badge,
        },
        "requiredItems": list(s.required_items or []),
        "place": s.place,
        "venueType": s.venue_type,
        "isFree": s.is_free,
        "source": s.source,
    }
