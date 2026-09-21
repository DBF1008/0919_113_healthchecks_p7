"""Plugin implementation of the Shell Command integration.

This is the reference TransportPlugin implementation: it shows how an
integration (including future ones such as Lark, WeCom or DingTalk) can
be packaged as a self-contained plugin with config validation, setup
form rendering, status rendering, notification and health check --
no changes to hc/api/models.py or hc/front/views.py required.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.template.loader import render_to_string
from pydantic import BaseModel

from hc.api.models import Channel, Flip
from hc.api.plugins import HealthStatus, NotificationResult, TransportPlugin
from hc.integrations.shell.transport import Shell


class ShellConfig(BaseModel):
    cmd_down: str = ""
    cmd_up: str = ""


class ShellPlugin(TransportPlugin):
    kind = "shell"
    label = "Shell Command"

    def validate_config(self, data: dict[str, Any]) -> BaseModel:
        return ShellConfig.model_validate(data)

    def render_setup_form(self, channel: Channel | None = None) -> str:
        config = {}
        if channel is not None:
            config = channel.get_config()
        return render_to_string("shell_setup_form.html", {"config": config})

    def render_status(self) -> str:
        return super().render_status()

    def notify(self, flip: Flip) -> NotificationResult:
        from hc.api import transports

        try:
            Shell(self.channel).notify(flip, notification=None)
        except transports.TransportError as e:
            return NotificationResult(
                success=False, error=e.message, permanent=e.permanent
            )

        return NotificationResult(success=True)

    def get_health(self) -> HealthStatus:
        if not settings.SHELL_ENABLED:
            return HealthStatus(healthy=False, message="SHELL_ENABLED is not set")
        return HealthStatus(healthy=True)

    def is_noop(self, status: str) -> bool:
        return Shell(self.channel).is_noop(status)
