"""Plugin architecture for notification transports.

This module defines the TransportPlugin abstract base class and the plugin
registry. Each integration (Slack, Telegram, Lark, WeCom, DingTalk, ...)
can be implemented as a TransportPlugin subclass and auto-discovered via
the "healthchecks.transports" setuptools entry points group, e.g.:

    # pyproject.toml of a third-party plugin package
    [project.entry-points."healthchecks.transports"]
    lark = "hc_lark.plugin:LarkPlugin"

Built-in integrations are registered in PluginRegistry.discover() and do
not need entry points. Legacy integrations (subclasses of
hc.api.transports.Transport listed in hc.api.models.TRANSPORTS) keep
working unchanged: they are wrapped in LegacyTransportPlugin adapters.
"""

from __future__ import annotations

import html
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from hc.api.models import Channel, Flip
    from hc.api.transports import Transport

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "healthchecks.transports"


@dataclass
class NotificationResult:
    """Outcome of a TransportPlugin.notify() call."""

    success: bool
    error: str = ""
    permanent: bool = False


@dataclass
class HealthStatus:
    """Result of a TransportPlugin health check."""

    healthy: bool
    message: str = ""


class RawConfig(BaseModel):
    """Fallback config model for plugins that do not define their own."""

    model_config = ConfigDict(extra="allow")


class TransportPlugin(ABC):
    """Base class for notification transport plugins.

    To add a new integration, subclass this, set `kind` and `label`,
    implement the abstract methods, and register the class either via
    the "healthchecks.transports" entry points group (for installable
    packages) or in PluginRegistry.discover() (for built-ins).
    """

    kind: str = ""
    label: str = ""

    is_legacy: bool = False

    def __init__(self, channel: Channel) -> None:
        self.channel = channel

    @abstractmethod
    def validate_config(self, data: dict[str, Any]) -> BaseModel:
        """Validate a configuration dict, return a parsed pydantic model.

        Raise pydantic.ValidationError (or ValueError) on invalid data.
        """

    @abstractmethod
    def notify(self, flip: Flip) -> NotificationResult:
        """Send a notification about a status flip."""

    def render_setup_form(self, channel: Channel | None = None) -> str:
        """Return an HTML fragment for the integration setup page.

        The default implementation returns an empty string, meaning
        "this plugin has no plugin-based setup form" and the caller
        should fall back to legacy per-kind views.
        """
        return ""

    def render_status(self) -> str:
        """Return an HTML fragment describing the channel's current status."""
        bits = []
        if self.channel.disabled:
            bits.append('<span class="status-disabled">Disabled</span>')
        if self.channel.last_error:
            bits.append(
                '<span class="status-error">Last error: %s</span>'
                % html.escape(self.channel.last_error)
            )

        if not bits:
            bits.append('<span class="status-ok">OK</span>')

        return " ".join(bits)

    def get_health(self) -> HealthStatus:
        """Report whether the integration itself is operational."""
        return HealthStatus(healthy=True)

    def is_noop(self, status: str) -> bool:
        """Return True if this plugin ignores the given check status."""
        return False

    def has_setup_form(self) -> bool:
        """Return True if this plugin renders its own setup form."""
        return type(self).render_setup_form is not TransportPlugin.render_setup_form

    def handle_setup(self, request: Any, channel: Channel) -> bool:
        """Validate and store configuration submitted via the setup form.

        The default implementation validates request.POST with
        validate_config() and stores the result in channel.config_json
        (and in the legacy channel.value field for backwards
        compatibility). Returns True on success.
        """
        data = {key: request.POST.get(key, "") for key in request.POST}
        data.pop("csrfmiddlewaretoken", None)
        config = self.validate_config(data)
        channel.config_json = config.model_dump(mode="json")
        channel.value = json.dumps(channel.config_json, sort_keys=True)
        return True


class LegacyTransportPlugin(TransportPlugin):
    """Adapter exposing a legacy Transport subclass as a plugin."""

    is_legacy = True

    def __init__(
        self, channel: Channel, transport_cls: type[Transport], kind: str, label: str
    ) -> None:
        super().__init__(channel)
        self.kind = kind
        self.label = label
        self._transport = transport_cls(channel)

    def validate_config(self, data: dict[str, Any]) -> BaseModel:
        return RawConfig.model_validate(data)

    def notify(self, flip: Flip) -> NotificationResult:
        from hc.api import transports

        try:
            self._transport.notify(flip, notification=None)
        except transports.TransportError as e:
            return NotificationResult(
                success=False, error=e.message, permanent=e.permanent
            )

        return NotificationResult(success=True)

    def is_noop(self, status: str) -> bool:
        # Delegate to the channel's transport property (rather than the
        # cached instance) so tests can patch hc.api.models.Channel.transport
        return self.channel.transport.is_noop(status)


class PluginRegistry:
    """Registry of available transport plugins.

    Plugins come from two sources:
    * built-in plugins, registered in _register_builtins()
    * third-party packages, discovered via the "healthchecks.transports"
      setuptools entry points group

    Discovery happens lazily on first access.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, type[TransportPlugin]] = {}
        self._discovered = False

    def register(self, plugin_cls: type[TransportPlugin]) -> None:
        if not plugin_cls.kind:
            raise ValueError(f"{plugin_cls!r} does not define a kind")
        self._plugins[plugin_cls.kind] = plugin_cls

    def unregister(self, kind: str) -> None:
        self._plugins.pop(kind, None)

    def _register_builtins(self) -> None:
        from hc.integrations.shell.plugin import ShellPlugin

        self.register(ShellPlugin)

    def _load_entry_points(self) -> None:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                self.register(ep.load())
            except Exception:
                logger.exception("Could not load transport plugin %r", ep.name)

    def discover(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        self._register_builtins()
        self._load_entry_points()

    def reset(self) -> None:
        """Forget discovered plugins (used by tests)."""
        self._plugins = {}
        self._discovered = False

    def get(self, kind: str) -> type[TransportPlugin] | None:
        self.discover()
        return self._plugins.get(kind)

    def __contains__(self, kind: str) -> bool:
        return self.get(kind) is not None

    def all(self) -> dict[str, type[TransportPlugin]]:
        self.discover()
        return dict(self._plugins)

    def choices(self) -> list[tuple[str, str]]:
        """(kind, label) pairs, for use as Django field choices."""
        return [(kind, cls.label) for kind, cls in sorted(self.all().items())]


registry = PluginRegistry()


def get_plugin(channel: Channel) -> TransportPlugin:
    """Return a plugin instance for a channel.

    New-style plugins (registered in the registry) take precedence.
    Channels of kinds that only exist in hc.api.models.TRANSPORTS are
    wrapped in LegacyTransportPlugin adapters. Raises NotImplementedError
    for unknown kinds.
    """
    plugin_cls = registry.get(channel.kind)
    if plugin_cls is not None:
        return plugin_cls(channel)

    from hc.api.models import TRANSPORTS

    if channel.kind in TRANSPORTS:
        label, _ = TRANSPORTS[channel.kind]
        return LegacyTransportPlugin(
            channel, channel.transport.__class__, channel.kind, label
        )

    raise NotImplementedError(f"Unknown channel kind: {channel.kind}")
