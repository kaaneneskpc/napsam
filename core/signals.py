"""
Yapilandirma degistiginde onbellegi dusur.

Bolum 7.2'nin kurali: butce kademeleri kod dagitimi gerektirmeden
degisebilmeli. Onbellek bu kurali gecikmeye cevirmesin diye, yonetim
panelinden ya da seed komutundan gelen her degisiklik onbellegi aninda
gecersiz kilar.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core import config
from core.models import BudgetTier, RemoteConfig, WeeklyTheme


@receiver(post_save, sender=BudgetTier)
@receiver(post_save, sender=RemoteConfig)
@receiver(post_save, sender=WeeklyTheme)
@receiver(post_delete, sender=BudgetTier)
@receiver(post_delete, sender=RemoteConfig)
@receiver(post_delete, sender=WeeklyTheme)
def _invalidate_config_cache(sender, **kwargs):
    config.invalidate()
