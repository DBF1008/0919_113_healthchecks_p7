from __future__ import annotations

import json
from datetime import timedelta as td

from django.utils.timezone import now
from pydantic import BaseModel

from hc.api.models import Channel, Check, Flip
from hc.api.plugins import (
    ENTRY_POINT_GROUP,
    HealthStatus,
    NotificationResult,
    TransportPlugin,
    registry,
)
from hc.test import BaseTestCase


class DummyConf(BaseModel):
    token: str


class DummyPlugin(TransportPlugin):
    kind = "dummy"
    label = "Dummy"
    config_attrs = ("dummy",)

    def validate_config(self, data):
        return DummyConf.model_validate(data)

    def render_setup_form(self):
        return "<form>dummy setup</form>"

    def render_status(self):
        return "<span>dummy ok</span>"

    def notify(self, flip, notification=None):
        return NotificationResult(success=True)

    def get_health(self):
        return HealthStatus(healthy=True, message="OK")


class PluginRegistryTestCase(BaseTestCase):
    def test_builtin_kinds_are_registered(self):
        kinds = dict(registry.kinds())
        for kind in ("email", "webhook", "shell", "telegram", "pd", "slack"):
            self.assertIn(kind, kinds)

    def test_entry_point_group_name(self):
        self.assertEqual(ENTRY_POINT_GROUP, "healthchecks.transports")

    def test_unknown_kind_raises(self):
        with self.assertRaises(NotImplementedError):
            registry.get("no-such-kind")

    def test_register_custom_plugin(self):
        registry.register(DummyPlugin)
        try:
            plugin = registry.get("dummy")
            self.assertIsInstance(plugin, DummyPlugin)
            self.assertIn(("dummy", "Dummy"), registry.kinds())
        finally:
            registry.unregister("dummy")

    def test_plugin_is_bound_to_channel(self):
        channel = Channel(kind="shell")
        self.assertIs(channel.plugin.channel, channel)


class DynamicConfigTestCase(BaseTestCase):
    def test_dynamic_config_attribute(self):
        channel = Channel(kind="shell")
        channel.value = json.dumps({"cmd_down": "echo down", "cmd_up": "echo up"})
        self.assertEqual(channel.shell.cmd_down, "echo down")
        self.assertEqual(channel.shell.cmd_up, "echo up")

    def test_config_json_takes_precedence_over_value(self):
        channel = Channel(kind="shell")
        channel.value = json.dumps({"cmd_down": "old", "cmd_up": ""})
        channel.config_json = {"cmd_down": "new", "cmd_up": ""}
        self.assertEqual(channel.get_config().cmd_down, "new")

    def test_shared_phone_attribute(self):
        for kind in ("call", "sms", "whatsapp", "signal"):
            channel = Channel(kind=kind)
            channel.value = json.dumps({"value": "+1234567890"})
            self.assertEqual(channel.phone.value, "+1234567890")

    def test_telegram_config(self):
        channel = Channel(kind="telegram")
        channel.value = json.dumps({"id": 123, "name": "Alice"})
        self.assertEqual(channel.telegram.id, 123)
        self.assertEqual(channel.telegram.name, "Alice")

    def test_pd_plain_service_key(self):
        channel = Channel(kind="pd")
        channel.value = "dummy-service-key"
        self.assertEqual(channel.pd.service_key, "dummy-service-key")

    def test_email_plain_address(self):
        channel = Channel(kind="email")
        channel.value = "alice@example.org"
        self.assertEqual(channel.email.value, "alice@example.org")
        self.assertTrue(channel.email.notify_up)

    def test_config_attr_of_wrong_kind_raises(self):
        channel = Channel(kind="email")
        channel.value = "alice@example.org"
        with self.assertRaises(AttributeError):
            channel.telegram

    def test_unknown_attribute_raises(self):
        channel = Channel(kind="email")
        with self.assertRaises(AttributeError):
            channel.no_such_attribute

    def test_custom_plugin_config_attr(self):
        registry.register(DummyPlugin)
        try:
            channel = Channel(kind="dummy")
            channel.config_json = {"token": "abc"}
            self.assertEqual(channel.dummy.token, "abc")
        finally:
            registry.unregister("dummy")


class PluginInterfaceTestCase(BaseTestCase):
    def test_render_setup_form_returns_html(self):
        channel = Channel(kind="shell")
        channel.value = json.dumps({"cmd_down": "echo down", "cmd_up": ""})
        html = channel.plugin.render_setup_form()
        self.assertIn("cmd_down", html)
        self.assertIn("echo down", html)

    def test_render_status_returns_html(self):
        channel = Channel(kind="email")
        channel.value = "alice@example.org"
        html = channel.plugin.render_status()
        self.assertIn("Email", html)

    def test_get_health(self):
        channel = Channel(kind="email")
        status = channel.plugin.get_health()
        self.assertIsInstance(status, HealthStatus)
        self.assertTrue(status.healthy)

    def test_notify_returns_result(self):
        check = Check(project=self.project)
        check.status = "paused"
        check.last_ping = now() - td(minutes=61)
        check.save()

        channel = Channel(project=self.project)
        channel.kind = "email"
        channel.value = "alice@example.org"
        channel.email_verified = True
        channel.save()
        channel.checks.add(check)

        flip = Flip(owner=check)
        flip.created = now()
        flip.old_status = "new"
        flip.new_status = "down"

        result = channel.plugin.notify(flip)
        self.assertIsInstance(result, NotificationResult)
        self.assertTrue(result.success)

    def test_channel_notify_dispatches_through_plugin(self):
        check = Check(project=self.project)
        check.status = "paused"
        check.last_ping = now() - td(minutes=61)
        check.save()

        channel = Channel(project=self.project)
        channel.kind = "email"
        channel.value = "alice@example.org"
        channel.email_verified = True
        channel.save()
        channel.checks.add(check)

        flip = Flip(owner=check)
        flip.created = now()
        flip.old_status = "new"
        flip.new_status = "down"

        error = channel.notify(flip)
        self.assertEqual(error, "")

        channel.refresh_from_db()
        self.assertEqual(channel.last_error, "")
        self.assertIsNotNone(channel.last_notify)

    def test_is_editable_uses_plugin_setup_view(self):
        self.assertTrue(Channel(kind="email").is_editable())
        self.assertTrue(Channel(kind="webhook").is_editable())
        self.assertFalse(Channel(kind="slack").is_editable())
