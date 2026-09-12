"""
AI sayaclarini kapsam tabanli hale getirir.

Eski sayac yalnizca cerezdeki anon_id'ye baglıydı ve bu bir koruma degildi:
cerez silmek ya da gizli sekme acmak sinirı sifirliyordu. Yeni yapi global,
IP ve cerez kapsamlarini ayni tabloda tutar.

Mevcut satirlar SILINIR. Bunlar yalnizca gunluk sayaclardir; tasinmalari
anlamsiz olurdu cunku eski kapsam ("anon_id") yeni semada karsiligi olmayan
bir bicimde tutuluyordu. Kaybedilen tek sey o gunun kismi sayimidir.
"""

from django.db import migrations, models


def sayaclari_temizle(apps, schema_editor):
    apps.get_model("core", "AiUsage").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0002_airequestcache_aiusage")]

    operations = [
        migrations.RunPython(sayaclari_temizle, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="aiusage", name="uniq_ai_usage_day"),
        migrations.RemoveField(model_name="aiusage", name="anon_id"),
        migrations.AddField(
            model_name="aiusage",
            name="scope",
            field=models.CharField(
                default="global",
                max_length=80,
                help_text="global | ip:<hash> | anon:<uuid>",
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="aiusage",
            name="count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddConstraint(
            model_name="aiusage",
            constraint=models.UniqueConstraint(
                fields=("scope", "day"), name="uniq_ai_usage_scope_day"
            ),
        ),
        migrations.AddIndex(
            model_name="aiusage",
            index=models.Index(fields=["day"], name="core_aiusag_day_638c64_idx"),
        ),
    ]
