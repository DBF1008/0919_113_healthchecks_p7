"""Plugin architecture for notification transports.

Each integration (Slack, Telegram, Lark, ...) is described by a
:class:`TransportPlugin` subclass. Plugins are discovered automatically:

* built-in integrations are seeded from ``_BUILTIN`` below, and any
  ``hc/integrations/<kind>/plugin.py`` module defining a ``plugin``
  attribute (a TransportPlugin subclass) is picked up automatically;
* third-party packages can register plugins through the
  ``healthchecks.transports`` setuptools entry point group.

Adding a new integration no longer requires editing ``hc/api/models.py``:
drop a plugin class in ``hc/integrations/<kind>/plugin.py`` (for built-ins)
or declare an entry point (for third-party packages)::

    # pyproject.toml of a third-party package
    [project.entry-points."healthchecks.transports"]
    lark = "hc_lark.plugin:LarkPlugin"

"""

from __future__ import annotations

import json
import logging
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Callable

from django.utils.html import format_html
from pydantic import BaseModel

from hc.api.transports import TransportError

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from hc.api.models import Channel, Flip, Notification

logger = logging.getLogger(__name__)

#: setuptools entry point group used for third-party transport plugins
ENTRY_POINT_GROUP = "healthchecks.transports"


@dataclass
class NotificationResult:
    """Outcome of a TransportPlugin.notify() call."""

    success: bool
    error: str = ""
    permanent: bool = False  # permanent errors disable the channel


@dataclass
class HealthStatus:
    """Result of a transport plugin self-check."""

    healthy: bool
    message: str = ""


def _import_attr(dotted_path: str) -> Any:
    modulename, classname = dotted_path.rsplit(".", maxsplit=1)
    return getattr(import_module(modulename), classname)


class TransportPlugin(ABC):
    """Abstract base class for notification transport plugins.

    A plugin instance is bound to at most one Channel (passed to the
    constructor). Instances are created on demand and are not shared
    between channels.
    """

    kind: str = ""
    label: str = ""
    #: Channel attribute names that resolve to this plugin's validated config
    #: (e.g. config_attrs=("telegram",) makes `channel.telegram` work).
    config_attrs: tuple[str, ...] = ()

    def __init__(self, channel: Channel | None = None) -> None:
        self.channel = channel

    @abstractmethod
    def validate_config(self, data: Any) -> BaseModel:
        """Validate raw configuration data and return a pydantic model."""

    @abstractmethod
    def render_setup_form(self) -> str:
        """Return an HTML fragment for the integration setup page."""

    @abstractmethod
    def render_status(self) -> str:
        """Return an HTML fragment showing the integration's status."""

    @abstractmethod
    def notify(
        self, flip: Flip, notification: Notification | None = None
    ) -> NotificationResult:
        """Send a notification about a check's status change."""

    @abstractmethod
    def get_health(self) -> HealthStatus:
        """Return the health status of this plugin (imports, credentials...)."""

    def is_noop(self, status: str) -> bool:
        """Return True if this plugin ignores the given check status."""
        return False

    def get_setup_view(
        self,
    ) -> Callable[[HttpRequest, Channel], HttpResponse] | None:
        """Return a custom add/edit view for this integration, if any."""
        return None


class LegacyTransportPlugin(TransportPlugin):
    """Adapter exposing a legacy transports.Transport subclass as a plugin.

    Subclass it (or let the registry build subclasses from the _BUILTIN
    seed table) by setting class attributes:

    * ``transport_path``: dotted path to the legacy Transport subclass
    * ``config_model``: dotted path to a pydantic model for validate_config
    * ``config_loader``: dotted path to a loader callable, for configs
      that are not plain JSON (e.g. PagerDuty's bare service key)
    * ``setup_view``: dotted path to a legacy add/edit view function
    """

    transport_path: str | None = None
    config_model: str | None = None
    config_loader: str | None = None
    config_strict: bool = False
    setup_view: str | None = None

    def get_transport_class(self) -> type | None:
        if self.transport_path is None:
            return None
        return _import_attr(self.transport_path)

    def get_transport(self) -> Any:
        cls = self.get_transport_class()
        if cls is None:
            raise NotImplementedError(f"Plugin '{self.kind}' has no transport class")
        return cls(self.channel)

    def validate_config(self, data: Any) -> BaseModel:
        if self.config_loader is not None:
            loader = _import_attr(self.config_loader)
            if not isinstance(data, str):
                data = json.dumps(data)
            return loader(data)
        if self.config_model is not None:
            model = _import_attr(self.config_model)
            if isinstance(data, str):
                return model.model_validate_json(data, strict=self.config_strict)
            return model.model_validate(data, strict=self.config_strict)
        raise NotImplementedError(
            f"Plugin '{self.kind}' does not define a config model"
        )

    def render_setup_form(self) -> str:
        return format_html(
            '<p class="plugin-setup">Configure this integration via the'
            ' "{}" setup form.</p>',
            self.label,
        )

    def render_status(self) -> str:
        channel = self.channel
        if channel is None:
            return ""
        if channel.disabled:
            return format_html(
                '<span class="plugin-status status-disabled">{}: disabled</span>',
                self.label,
            )
        if channel.last_error:
            return format_html(
                '<span class="plugin-status status-error">{}: {}</span>',
                self.label,
                channel.last_error,
            )
        return format_html(
            '<span class="plugin-status status-ok">{}: OK</span>', self.label
        )

    def notify(
        self, flip: Flip, notification: Notification | None = None
    ) -> NotificationResult:
        try:
            self.get_transport().notify(flip, notification)
        except TransportError as e:
            return NotificationResult(
                success=False, error=e.message, permanent=e.permanent
            )
        return NotificationResult(success=True)

    def is_noop(self, status: str) -> bool:
        if self.transport_path is None:
            return False
        return self.get_transport().is_noop(status)

    def get_health(self) -> HealthStatus:
        try:
            self.get_transport_class()
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
        return HealthStatus(healthy=True, message="OK")

    def get_setup_view(self) -> Callable | None:
        if self.setup_view is None:
            return None
        return _import_attr(self.setup_view)


# Seed table for the built-in integrations. This is *not* a registry:
# entries are only used to build LegacyTransportPlugin subclasses, and any
# hc/integrations/<kind>/plugin.py module takes precedence over them.
_BUILTIN: dict[str, dict[str, Any]] = {
    "apprise": {
        "label": "Apprise",
        "transport_path": "hc.integrations.apprise.transport.Apprise",
    },
    "call": {
        "label": "Phone Call",
        "transport_path": "hc.integrations.call.transport.Call",
        "config_attrs": ("phone",),
        "config_model": "hc.api.models.PhoneConf",
    },
    "discord": {
        "label": "Discord",
        "transport_path": "hc.integrations.discord.transport.Discord",
    },
    "email": {
        "label": "Email",
        "transport_path": "hc.integrations.email.transport.Email",
        "config_attrs": ("email",),
        "config_loader": "hc.api.models.EmailConf.load",
        "setup_view": "hc.integrations.email.views.email_form",
    },
    "github": {
        "label": "GitHub",
        "transport_path": "hc.integrations.github.transport.GitHub",
        "config_attrs": ("github",),
        "config_model": "hc.api.models.GitHubConf",
    },
    "googlechat": {
        "label": "Google Chat",
        "transport_path": "hc.integrations.googlechat.transport.GoogleChat",
    },
    "gotify": {
        "label": "Gotify",
        "transport_path": "hc.integrations.gotify.transport.Gotify",
        "config_attrs": ("gotify",),
        "config_model": "hc.api.models.GotifyConf",
        "config_strict": True,
    },
    "group": {
        "label": "Group",
        "transport_path": "hc.integrations.group.transport.Group",
        "setup_view": "hc.integrations.group.views.group_form",
    },
    "matrix": {
        "label": "Matrix",
        "transport_path": "hc.integrations.matrix.transport.Matrix",
    },
    "mattermost": {
        "label": "Mattermost",
        "transport_path": "hc.integrations.mattermost.transport.Mattermost",
    },
    "msteamsw": {
        "label": "Microsoft Teams",
        "transport_path": "hc.integrations.msteamsw.transport.MsTeamsWorkflow",
    },
    "ntfy": {
        "label": "ntfy",
        "transport_path": "hc.integrations.ntfy.transport.Ntfy",
        "config_attrs": ("ntfy",),
        "config_model": "hc.api.models.NtfyConf",
        "config_strict": True,
        "setup_view": "hc.integrations.ntfy.views.ntfy_form",
    },
    "opsgenie": {
        "label": "Opsgenie",
        "transport_path": "hc.integrations.opsgenie.transport.Opsgenie",
        "config_attrs": ("opsgenie",),
        "config_model": "hc.api.models.OpsgenieConf",
    },
    "pagertree": {
        "label": "PagerTree",
        "transport_path": "hc.integrations.pagertree.transport.PagerTree",
    },
    "pd": {
        "label": "PagerDuty",
        "transport_path": "hc.integrations.pd.transport.PagerDuty",
        "config_attrs": ("pd",),
        "config_loader": "hc.api.models.PdConf.load",
    },
    "po": {
        "label": "Pushover",
        "transport_path": "hc.integrations.po.transport.Pushover",
    },
    "pushbullet": {
        "label": "Pushbullet",
        "transport_path": "hc.integrations.pushbullet.transport.Pushbullet",
    },
    "rocketchat": {
        "label": "Rocket.Chat",
        "transport_path": "hc.integrations.rocketchat.transport.RocketChat",
    },
    "shell": {
        "label": "Shell Command",
        "transport_path": "hc.integrations.shell.transport.Shell",
        "config_attrs": ("shell",),
        "config_model": "hc.api.models.ShellConf",
    },
    "signal": {
        "label": "Signal",
        "transport_path": "hc.integrations.signal.transport.Signal",
        "config_attrs": ("phone",),
        "config_model": "hc.api.models.PhoneConf",
        "setup_view": "hc.integrations.signal.views.signal_form",
    },
    "slack": {
        "label": "Slack",
        "transport_path": "hc.integrations.slack.transport.Slack",
    },
    "sms": {
        "label": "SMS",
        "transport_path": "hc.integrations.sms.transport.Sms",
        "config_attrs": ("phone",),
        "config_model": "hc.api.models.PhoneConf",
        "setup_view": "hc.integrations.sms.views.sms_form",
    },
    "spike": {
        "label": "Spike",
        "transport_path": "hc.integrations.spike.transport.Spike",
    },
    "telegram": {
        "label": "Telegram",
        "transport_path": "hc.integrations.telegram.transport.Telegram",
        "config_attrs": ("telegram",),
        "config_model": "hc.api.models.TelegramConf",
    },
    "trello": {
        "label": "Trello",
        "transport_path": "hc.integrations.trello.transport.Trello",
        "config_attrs": ("trello",),
        "config_model": "hc.api.models.TrelloConf",
        "config_strict": True,
    },
    "victorops": {
        "label": "Splunk On-Call",
        "transport_path": "hc.integrations.victorops.transport.VictorOps",
    },
    "webhook": {
        "label": "Webhook",
        "transport_path": "hc.integrations.webhook.transport.Webhook",
        "setup_view": "hc.integrations.webhook.views.webhook_form",
    },
    "whatsapp": {
        "label": "WhatsApp",
        "transport_path": "hc.integrations.whatsapp.transport.WhatsApp",
        "config_attrs": ("phone",),
        "config_model": "hc.api.models.PhoneConf",
        "setup_view": "hc.integrations.whatsapp.views.whatsapp_form",
    },
    "zulip": {
        "label": "Zulip",
        "transport_path": "hc.integrations.zulip.transport.Zulip",
        "config_attrs": ("zulip",),
        "config_model": "hc.api.models.ZulipConf",
    },
}


def _legacy_plugin_class(kind: str, spec: dict[str, Any]) -> type:
    """Build a LegacyTransportPlugin subclass from a _BUILTIN spec."""
    attrs = {"kind": kind}
    attrs.update(spec)
    return type(f"{kind.title().replace('_', '')}Plugin", (LegacyTransportPlugin,), attrs)


class PluginRegistry:
    """Registry of available transport plugins, loaded lazily on first use."""

    def __init__(self) -> None:
        self._plugins: dict[str, type[TransportPlugin]] = {}
        self._loaded = False
        self._loading = False

    def register(self, plugin_cls: type[TransportPlugin]) -> None:
        """Register a TransportPlugin subclass."""
        if not plugin_cls.kind:
            raise ValueError(f"{plugin_cls!r} does not define a 'kind'")
        self._plugins[plugin_cls.kind] = plugin_cls

    def unregister(self, kind: str) -> None:
        self._plugins.pop(kind, None)

    def load(self) -> None:
        """Discover plugins. Runs at most once; safe to call repeatedly."""
        if self._loaded or self._loading:
            return
        self._loading = True
        try:
            for kind, spec in _BUILTIN.items():
                self.register(_legacy_plugin_class(kind, spec))
            self._load_discovered()
            self._load_entry_points()
            self._loaded = True
        finally:
            self._loading = False

    def _load_discovered(self) -> None:
        """Pick up hc/integrations/<kind>/plugin.py modules."""
        import hc.integrations

        for info in pkgutil.iter_modules(hc.integrations.__path__):
            modulename = f"hc.integrations.{info.name}.plugin"
            try:
                module = import_module(modulename)
            except ModuleNotFoundError as e:
                if e.name == modulename:
                    continue  # no plugin.py in this integration, that's fine
                logger.exception("Could not import %s", modulename)
                continue
            except Exception:
                logger.exception("Could not import %s", modulename)
                continue

            plugin = getattr(module, "plugin", None)
            if plugin is None:
                continue
            cls = plugin if isinstance(plugin, type) else type(plugin)
            self.register(cls)

    def _load_entry_points(self) -> None:
        """Load third-party plugins from the healthchecks.transports group."""
        try:
            eps = entry_points(group=ENTRY_POINT_GROUP)
        except TypeError:  # very old importlib.metadata API
            eps = entry_points().get(ENTRY_POINT_GROUP, ())  # type: ignore[union-attr]

        for ep in eps:
            try:
                obj = ep.load()
                cls = obj if isinstance(obj, type) else type(obj)
                self.register(cls)
            except Exception:
                logger.exception(
                    "Could not load transport plugin entry point %r", ep.name
                )

    def get(self, kind: str, channel: Channel | None = None) -> TransportPlugin:
        """Return a plugin instance for the given kind, bound to `channel`."""
        self.load()
        try:
            cls = self._plugins[kind]
        except KeyError:
            raise NotImplementedError(f"Unknown channel kind: {kind}") from None
        return cls(channel)

    def kinds(self) -> list[tuple[str, str]]:
        """Return [(kind, label), ...] choices for all registered plugins."""
        self.load()
        return sorted((kind, cls.label) for kind, cls in self._plugins.items())

    def config_attr_map(self) -> dict[str, tuple[str, ...]]:
        """Return {channel attribute name: (kinds it applies to)}."""
        if self._loading:
            # Guard against re-entrant access while plugins are being imported
            return {}
        self.load()
        result: dict[str, list[str]] = {}
        for kind, cls in self._plugins.items():
            for attr in cls.config_attrs:
                result.setdefault(attr, []).append(kind)
        return {attr: tuple(kinds) for attr, kinds in result.items()}

    def transport_map(self) -> dict[str, tuple[str, str]]:
        """Return {kind: (label, transport dotted path)} for legacy transports."""
        self.load()
        result = {}
        for kind, cls in self._plugins.items():
            path = getattr(cls, "transport_path", None)
            if path:
                result[kind] = (cls.label, path)
        return result


#: Process-wide plugin registry singleton
registry = PluginRegistry()


def get_channel_kinds() -> list[tuple[str, str]]:
    """Choices callable for the Channel.kind field (evaluated lazily)."""
    return registry.kinds()
