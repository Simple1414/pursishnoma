"""The platform is now called «Андеша»; rename the organisation if it still has the old default."""

from django.db import migrations, models


def rename(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(organization_name="ResearchSurvey").update(organization_name="Андеша")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_tagline_optional"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="organization_name",
            field=models.CharField(
                default="Андеша", max_length=255, verbose_name="organization name"
            ),
        ),
        migrations.RunPython(rename, migrations.RunPython.noop),
    ]
