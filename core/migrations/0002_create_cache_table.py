from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # The database cache backs rate limiting across gunicorn workers.
    call_command("createcachetable", verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
