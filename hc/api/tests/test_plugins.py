from __future__ import annotations

import json
from unittest.mock import patch

from hc.api.models import (
    TRANSPORTS,
    Channel,
    get_channel_kinds,
    load_plugin_transports,
)
from hc.api.plugins import (
    HealthStatus,
    LegacyTransportPlugin,
    NotificationResult,
    PluginRegistry,
    TransportPlugin,
    registry,
)
from hc.integrations.shell.plugin import ShellPlugin
from hc.test import BaseTestCase


class PluginRegistryTestCase(BaseTestCase):
    def test_registry_discovers_builtin_shell_plugin(self) -> None:
        self.assertIn("shell", registry)
        self.assertIs(registry.get("shell"), ShellPlugin)

    def test_registry_returns_none_for_unknown_kind(self) -> None:
        self.assertIsNone(registry.get("nope"))
        self.assertNotIn("nope", registry)

    def test_choices_returns_kind_label_pairs(self) -> None:
        choices = registry.choices()
        self.assertIn(("shell", "Shell Command"), choices)

    def test_register_requires_kind(self) -> None:
        class NoKind(TransportPlugin):
            def validate_config(self, data):
                raise NotImplementedError

            def notify(self, flip):
                raise NotImplementedError

        with self.assertRaises(ValueError):
            PluginRegistry().register(NoKind)

    def test_load_plugin_transports_merges_into_TRANSPORTS(self) -> None:
        load_plugin_transports()
        self.assertIn("shell", TRANSPORTS)
        label, cls = TRANSPORTS["shell"]
        self.assertEqual(label, "Shell Command")

    def test_get_channel_kinds_includes_plugins(self) -> None:
        kinds = dict(get_channel_kinds())
        self.assertEqual(kinds["shell"], "Shell Command")

        self.assertEqual(kinds["email"], "Email")


class EntryPointDiscoveryTestCase(BaseTestCase):
    def test_discovers_entry_point_plugins(self) -> None:
        class LarkPlugin(TransportPlugin):
            kind = "lark"
            label = "Lark"

            def validate_config(self, data):
                raise NotImplementedError

            def notify(self, flip):
                raise NotImplementedError

        class FakeEntryPoint:
            name = "lark"

            def load(self):
                return LarkPlugin

        reg = PluginRegistry()
        with patch("hc.api.plugins.entry_points", return_value=[FakeEntryPoint()]):
            reg.discover()

        self.assertIs(reg.get("lark"), LarkPlugin)


class PluginInterfaceTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.channel = Channel(project=self.project, kind="shell")
        self.channel.value = json.dumps({"cmd_down": "echo down", "cmd_up": ""})
        self.channel.save()

    def test_get_plugin_returns_plugin_instance(self) -> None:
        plugin = self.channel.get_plugin()
        self.assertIsInstance(plugin, ShellPlugin)
        self.assertFalse(plugin.is_legacy)

    def test_get_plugin_wraps_legacy_transports(self) -> None:
        channel = Channel(project=self.project, kind="email")
        channel.value = "alice@example.org"
        plugin = channel.get_plugin()
        self.assertIsInstance(plugin, LegacyTransportPlugin)
        self.assertTrue(plugin.is_legacy)

    def test_get_plugin_raises_for_unknown_kind(self) -> None:
        channel = Channel(project=self.project, kind="nope")
        with self.assertRaises(NotImplementedError):
            channel.get_plugin()

    def test_validate_config(self) -> None:
        plugin = self.channel.get_plugin()
        config = plugin.validate_config({"cmd_down": "echo hi", "cmd_up": ""})
        self.assertEqual(config.cmd_down, "echo hi")

    def test_validate_config_rejects_bad_data(self) -> None:
        plugin = self.channel.get_plugin()
        with self.assertRaises(ValueError):
            plugin.validate_config({"cmd_down": 123})

    def test_render_setup_form_returns_html(self) -> None:
        plugin = self.channel.get_plugin()
        html = plugin.render_setup_form(self.channel)
        self.assertIn("cmd_down", html)
        self.assertIn("echo down", html)

    def test_render_status(self) -> None:
        plugin = self.channel.get_plugin()
        self.assertIn("OK", plugin.render_status())

        self.channel.disabled = True
        self.assertIn("Disabled", plugin.render_status())

    def test_get_health(self) -> None:
        plugin = self.channel.get_plugin()
        status = plugin.get_health()
        self.assertIsInstance(status, HealthStatus)

        self.assertFalse(status.healthy)

    def test_notification_result_defaults(self) -> None:
        result = NotificationResult(success=True)
        self.assertEqual(result.error, "")
        self.assertFalse(result.permanent)


class DynamicConfigTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.channel = Channel(project=self.project, kind="shell")
        self.channel.value = json.dumps({"cmd_down": "echo down", "cmd_up": "echo up"})
        self.channel.save()

    def test_conf_kind_resolves_dynamically(self) -> None:
        conf = self.channel.conf_shell
        self.assertEqual(conf.cmd_down, "echo down")
        self.assertEqual(conf.cmd_up, "echo up")

    def test_conf_property_uses_own_kind(self) -> None:
        conf = self.channel.conf
        self.assertEqual(conf.cmd_down, "echo down")

    def test_conf_prefers_config_json(self) -> None:
        self.channel.config_json = {"cmd_down": "echo json", "cmd_up": ""}
        conf = self.channel.conf
        self.assertEqual(conf.cmd_down, "echo json")

    def test_unknown_conf_attribute_raises(self) -> None:
        with self.assertRaises(AttributeError):
            self.channel.conf_nope

    def test_get_config_parses_legacy_value(self) -> None:
        self.assertEqual(
            self.channel.get_config(), {"cmd_down": "echo down", "cmd_up": "echo up"}
        )

    def test_get_config_handles_non_json_value(self) -> None:
        channel = Channel(kind="email", value="alice@example.org")
        self.assertEqual(channel.get_config(), {})

    def test_set_config_validates_and_syncs_value(self) -> None:
        self.channel.set_config({"cmd_down": "echo new", "cmd_up": ""})
        self.assertEqual(
            self.channel.config_json, {"cmd_down": "echo new", "cmd_up": ""}
        )
        self.assertEqual(
            json.loads(self.channel.value), {"cmd_down": "echo new", "cmd_up": ""}
        )

    def test_set_config_rejects_invalid_data(self) -> None:
        with self.assertRaises(ValueError):
            self.channel.set_config({"cmd_down": 123})


class NotifyDispatchTestCase(BaseTestCase):
    def test_notify_uses_plugin_and_records_result(self) -> None:
        channel = Channel(project=self.project, kind="shell")
        channel.value = json.dumps({"cmd_down": "true", "cmd_up": ""})
        channel.save()

        from hc.api.models import Check, Flip

        check = Check(project=self.project, status="down")
        check.save()
        flip = Flip(owner=check, old_status="up", new_status="down")
        flip.created = check.created

        with self.settings(SHELL_ENABLED=True):
            error = channel.notify(flip, is_test=True)

        self.assertEqual(error, "")

        channel.refresh_from_db()
        self.assertEqual(channel.last_error, "")
        self.assertIsNotNone(channel.last_notify)
