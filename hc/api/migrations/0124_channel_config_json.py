import json

from django.db import migrations, models

import hc.api.models


def forwards(apps, schema_editor):
    Channel = apps.get_model("api", "Channel")
    for channel in Channel.objects.all().iterator():
        value = channel.value
        if not value:
            continue
        try:
            doc = json.loads(value)
        except ValueError:
            # Not a JSON document (e.g. a plain email address or a phone
            # number): leave config_json empty, Channel.get_config() will
            # keep falling back to the legacy value field.
            continue
        if isinstance(doc, dict):
            channel.config_json = doc
            channel.save(update_fields=["config_json"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0123_alter_channel_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="config_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="channel",
            name="kind",
            field=models.CharField(
                choices=hc.api.models.get_channel_kinds, max_length=20
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
