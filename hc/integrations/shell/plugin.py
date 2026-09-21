"""Shell Command transport plugin.

Discovered automatically by hc.api.plugins.registry (no manual
registration needed): any hc/integrations/<kind>/plugin.py module
defining a `plugin` attribute is picked up on first use.
"""

from __future__ import annotations

from django.utils.html import format_html

from hc.api.plugins import HealthStatus, LegacyTransportPlugin


class ShellPlugin(LegacyTransportPlugin):
    kind = "shell"
    label = "Shell Command"
    transport_path = "hc.integrations.shell.transport.Shell"
    config_attrs = ("shell",)
    config_model = "hc.api.models.ShellConf"

    def render_setup_form(self) -> str:
        cmd_down, cmd_up = "", ""
        if self.channel is not None and self.channel.value:
            try:
                conf = self.channel.get_config()
                cmd_down, cmd_up = conf.cmd_down, conf.cmd_up
            except Exception:
                pass

        return format_html(
            '<fieldset class="shell-setup">'
            "<label>Command to run when a check goes down</label>"
            '<input type="text" name="cmd_down" value="{}" maxlength="1000">'
            "<label>Command to run when a check goes back up</label>"
            '<input type="text" name="cmd_up" value="{}" maxlength="1000">'
            "</fieldset>",
            cmd_down,
            cmd_up,
        )

    def render_status(self) -> str:
        if self.channel is None:
            return ""
        try:
            conf = self.channel.get_config()
        except Exception:
            return format_html(
                '<span class="plugin-status status-error">'
                "Shell Command: invalid configuration</span>"
            )
        return format_html(
            '<span class="plugin-status status-ok">Shell Command: down={!r},'
            " up={!r}</span>",
            conf.cmd_down,
            conf.cmd_up,
        )

    def get_health(self) -> HealthStatus:
        from django.conf import settings

        if not settings.SHELL_ENABLED:
            return HealthStatus(healthy=False, message="SHELL_ENABLED is not set")
        return super().get_health()


plugin = ShellPlugin
