# Generated as part of the transport plugin architecture refactor.

from __future__ import annotations

import json

from django.db import migrations, models

import hc.api.models


def backfill_config_json(apps, schema_editor):
    """Parse the legacy `value` field into structured `config_json` data.

    Only JSON-object values are migrated; non-JSON legacy formats (e.g.
    PagerDuty's bare service key, Pushover's "key|priority" strings) are
    left in `value` and keep working through the plugins' config loaders.
    """
    Channel = apps.get_model("api", "Channel")
    for channel in Channel.objects.all().iterator():
        value = (channel.value or "").strip()
        if not value.startswith("{"):
            continue
        try:
            data = json.loads(value)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        channel.config_json = data
        channel.save(update_fields=["config_json"])


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
        migrations.RunPython(backfill_config_json, migrations.RunPython.noop),
    ]
