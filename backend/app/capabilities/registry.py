"""Capability registry: manifests + saved config → the API ``Capability`` view.

Also merges config updates (saved or draft), normalises endpoint addresses and resolves the
endpoint an adapter should call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from app.capabilities.bundled import BundledKind, ProviderRegistry
from app.capabilities.manifest import Manifest, PresetSpec, load_manifests
from app.capabilities.schemas import (
    BundledOut,
    CapabilityConfig,
    CapabilityConfigUpdate,
    CapabilityOut,
    FieldOut,
    Locality,
    Option,
    PresetOut,
    Status,
)
from app.config import CapabilitySettings, Paths, Settings, SettingsStore, TestResult, key_hint

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
_BUNDLED_KINDS: dict[str, BundledKind] = {
    "embeddings": "embeddings",
    "reranker": "reranker",
    "grounding": "grounding",
    "stt": "stt",
}
_STT_MODES = (
    Option(value="bundled", label="Built in (Whistle, runs on this computer)"),
    Option(value="endpoint", label="Endpoint (OpenAI-compatible)"),
)


class CapabilityNotFoundError(LookupError):
    """Unknown capability id."""


class ConfigError(ValueError):
    """An update or draft is invalid, or the config is incomplete. Message is plain English."""


@dataclass(frozen=True)
class EndpointConfig:
    """Everything an endpoint adapter needs. ``api_key`` is never logged or returned."""

    capability: str
    adapter: str
    base_url: str | None
    model: str | None
    api_version: str | None = None
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Evaluation:
    status: Status
    detail: str
    locality: Locality | None


def locality_for_url(url: str | None) -> Locality | None:
    """``local`` for localhost-style hosts, ``remote`` for anything else (LAN included)."""
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    return "local" if host in LOCAL_HOSTS else "remote"


def normalise_base_url(url: str, adapter: str) -> str:
    """Validate and normalise an endpoint address for ``adapter``.

    OpenAI-compatible: strip the trailing slash; a bare host gets ``/v1``.
    Anthropic: strip a trailing ``/v1``. Azure: strip anything from ``/openai`` on.
    ``plain`` (SearXNG): only strip the trailing slash.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("Enter a full web address starting with http:// or https://.")
    path = parts.path.rstrip("/")
    if adapter == "anthropic":
        path = path.removesuffix("/v1")
    elif adapter == "azure_openai":
        index = path.find("/openai")
        if index >= 0:
            path = path[:index]
    elif adapter != "plain" and not path:
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _missing_phrase(name: str, azure: bool) -> str:
    phrases = {
        "base_url": "Azure endpoint" if azure else "address",
        "model": "deployment name" if azure else "model name",
        "api_key": "API key",
        "api_version": "API version",
    }
    return phrases.get(name, name)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _origin(url: str | None) -> tuple[str, str, int | None] | None:
    """Scheme, host and port of ``url`` — the parts that decide where a key is sent."""
    if not url:
        return None
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return (parts.scheme.lower(), (parts.hostname or "").lower(), port)


# Used when a capability has presets but does not show a preset field (speech-to-text).
_IMPLICIT_PRESET = PresetSpec(
    id="custom", label="OpenAI-compatible", adapter="openai_compatible", needs_api_key=False
)


class CapabilityRegistry:
    """Builds capability views and applies config changes."""

    def __init__(
        self,
        paths: Paths,
        store: SettingsStore,
        providers: ProviderRegistry,
        manifests: dict[str, Manifest] | None = None,
    ) -> None:
        self._paths = paths
        self._store = store
        self._providers = providers
        self._manifests = manifests if manifests is not None else load_manifests()

    # --- lookups ---------------------------------------------------------------------------

    def ids(self) -> list[str]:
        return list(self._manifests)

    def manifest(self, cap_id: str) -> Manifest:
        try:
            return self._manifests[cap_id]
        except KeyError:
            raise CapabilityNotFoundError(cap_id) from None

    def presets(self, cap_id: str) -> list[PresetSpec]:
        return [p for p in self.manifest(cap_id).presets if not p.mock_only or self._paths.mock_llm]

    def providers(self, cap_id: str) -> list[Option]:
        """Selectable services (test-only ones only with ``TRUSTBOOK_MOCK_LLM=1``)."""
        return [
            Option(value=p.value, label=p.label)
            for p in self.manifest(cap_id).providers
            if not p.mock_only or self._paths.mock_llm
        ]

    def preset(self, cap_id: str, preset_id: str | None) -> PresetSpec | None:
        return next((p for p in self.presets(cap_id) if p.id == preset_id), None)

    def effective_preset(self, cap_id: str, s: CapabilitySettings) -> PresetSpec | None:
        """The saved preset, or the implicit OpenAI-compatible one when no preset field exists."""
        preset = self.preset(cap_id, s.preset)
        if preset is None and "preset" not in self.manifest(cap_id).ui_fields:
            return _IMPLICIT_PRESET
        return preset

    def stored(self, cap_id: str) -> CapabilitySettings:
        self.manifest(cap_id)
        return self._store.load().capability(cap_id)

    # --- views -----------------------------------------------------------------------------

    def list_all(self) -> list[CapabilityOut]:
        return [self.get(cap_id) for cap_id in self._manifests]

    def get(self, cap_id: str) -> CapabilityOut:
        manifest = self.manifest(cap_id)
        settings = self.stored(cap_id)
        evaluation = self.evaluate(cap_id, settings)
        bundled_kind = _BUNDLED_KINDS.get(cap_id)
        bundled = None
        if bundled_kind is not None:
            info = self._providers.info(bundled_kind)
            bundled = BundledOut(
                model_id=info.model_id,
                revision=info.revision,
                license=info.license,
                present=info.present,
                fallback=info.fallback,
            )
        return CapabilityOut(
            id=manifest.id,
            label=manifest.label,
            description=manifest.description,
            kind=manifest.kind,
            required=manifest.required,
            hidden=manifest.hidden,
            contract=manifest.contract,
            fields=self.fields(cap_id, settings),
            presets=[
                PresetOut(
                    id=p.id,
                    label=p.label,
                    adapter=p.adapter,
                    base_url=p.base_url,
                    needs_api_key=p.needs_api_key,
                )
                for p in self.presets(cap_id)
            ],
            providers=self.providers(cap_id),
            config=self._config_out(cap_id, settings),
            locality=evaluation.locality,
            status=evaluation.status,
            status_detail=evaluation.detail,
            last_test=settings.last_test,
            bundled=bundled,
        )

    def _config_out(self, cap_id: str, s: CapabilitySettings) -> CapabilityConfig:
        return CapabilityConfig(
            enabled=s.enabled,
            preset=s.preset,
            base_url=s.base_url,
            model=s.model,
            api_version=s.api_version,
            api_key_set=bool(s.api_key),
            api_key_hint=key_hint(s.api_key),
            use_chat=bool(s.use_chat) if cap_id == "gate" else None,
            provider=s.provider if cap_id == "web_search" else None,
            mode=s.mode if cap_id == "stt" else None,
            code_judge=s.code_judge is not False if cap_id == "grounding" else None,
        )

    # --- fields ----------------------------------------------------------------------------

    def _uses_endpoint(self, cap_id: str, s: CapabilitySettings) -> bool:
        """True when the endpoint fields (preset, address, key, model) apply."""
        if cap_id == "gate":
            return not s.use_chat
        if cap_id == "stt":
            return s.mode == "endpoint"
        return self.manifest(cap_id).kind == "endpoint"

    def applicable_fields(self, cap_id: str, s: CapabilitySettings) -> list[str]:
        """Names of the fields that apply to the saved state ``s`` (drives status only).

        The API always returns every field (see :meth:`fields`); the UI hides fields itself.
        """
        manifest = self.manifest(cap_id)
        preset = self.effective_preset(cap_id, s)
        endpoint = self._uses_endpoint(cap_id, s)
        names: list[str] = []
        for name in manifest.ui_fields:
            if name in ("enabled", "use_chat", "mode", "provider", "code_judge"):
                names.append(name)
            elif name == "preset" and endpoint:
                names.append(name)
            elif name == "base_url" and cap_id == "web_search":
                if (s.provider or "searxng") == "searxng":
                    names.append(name)
            elif name in ("base_url", "model") and endpoint and preset and preset.adapter != "mock":
                names.append(name)
            elif name == "api_key":
                if cap_id == "web_search":
                    if s.provider in ("brave", "tavily"):
                        names.append(name)
                elif endpoint and preset and preset.adapter != "mock":
                    names.append(name)
            elif name == "api_version" and endpoint and preset and preset.adapter == "azure_openai":
                names.append(name)
        return names

    def _required(self, cap_id: str, name: str, s: CapabilitySettings) -> bool:
        if name in ("enabled", "use_chat", "code_judge"):
            return False
        if cap_id == "web_search":
            return name != "base_url"
        if name == "api_key":
            preset = self.effective_preset(cap_id, s)
            return bool(preset and preset.needs_api_key)
        return True

    def fields(self, cap_id: str, s: CapabilitySettings) -> list[FieldOut]:
        """Every field the capability can show (the UI hides the ones that do not apply)."""
        manifest = self.manifest(cap_id)
        search = cap_id == "web_search"
        result: list[FieldOut] = []
        for name in manifest.ui_fields:
            required = self._required(cap_id, name, s)
            spec: FieldOut
            if name == "enabled":
                spec = FieldOut(name=name, label="Enabled", type="toggle", required=False)
            elif name == "code_judge":
                spec = FieldOut(
                    name=name,
                    label="Check code claims with the chat model",
                    type="toggle",
                    required=False,
                )
            elif name == "use_chat":
                spec = FieldOut(
                    name=name,
                    label="Use the chat model",
                    type="toggle",
                    required=False,
                    help="Turn off to use a separate, usually smaller model.",
                )
            elif name == "mode":
                spec = FieldOut(
                    name=name,
                    label="Runs on",
                    type="select",
                    required=True,
                    options=list(_STT_MODES),
                )
            elif name == "provider":
                spec = FieldOut(
                    name=name,
                    label="Search service",
                    type="select",
                    required=True,
                    options=self.providers(cap_id),
                )
            elif name == "preset":
                spec = FieldOut(
                    name=name,
                    label="Provider",
                    type="select",
                    required=True,
                    options=[Option(value=p.id, label=p.label) for p in self.presets(cap_id)],
                )
            elif name == "base_url" and search:
                spec = FieldOut(
                    name=name,
                    label="SearXNG address",
                    type="url",
                    required=False,
                    placeholder=self._paths.searxng_url,
                    help="Leave empty to use the built-in SearXNG.",
                )
            elif name == "base_url":
                spec = FieldOut(
                    name=name,
                    label="Address",
                    type="url",
                    required=True,
                    placeholder="https://…/v1",
                    help=(
                        "The endpoint's base address, usually ending in /v1. For Azure, the "
                        "resource endpoint, e.g. https://my-resource.openai.azure.com."
                    ),
                )
            elif name == "model":
                spec = FieldOut(
                    name=name,
                    label="Model",
                    type="text",
                    required=True,
                    placeholder="Model name",
                    help="For Azure, the deployment name.",
                )
            elif name == "api_version":
                spec = FieldOut(
                    name=name,
                    label="API version",
                    type="text",
                    required=True,
                    placeholder="2024-10-21",
                    help="Azure only.",
                )
            else:  # api_key
                hint = key_hint(s.api_key)
                spec = FieldOut(
                    name=name,
                    label="API key",
                    type="password",
                    required=required,
                    placeholder=f"Saved ({hint}). Leave empty to keep it." if hint else None,
                    help=(
                        "Needed for Brave Search and Tavily. Stored only on this computer."
                        if search
                        else "Stored only on this computer, never inside a space."
                    ),
                )
            override = manifest.field_overrides.get(name)
            if override is not None:
                spec = spec.model_copy(update=override.model_dump(exclude_none=True))
            result.append(spec)
        return result

    # --- status ----------------------------------------------------------------------------

    def missing_fields(self, cap_id: str, s: CapabilitySettings) -> list[str]:
        """Names of required, applicable fields that have no value (``preset`` first)."""
        if self._uses_endpoint(cap_id, s) and self.effective_preset(cap_id, s) is None:
            return ["preset"]
        missing: list[str] = []
        for name in self.applicable_fields(cap_id, s):
            if not self._required(cap_id, name, s) or name in ("mode", "provider", "preset"):
                continue
            if not getattr(s, name):
                missing.append(name)
        return missing

    def evaluate(self, cap_id: str, s: CapabilitySettings) -> Evaluation:
        """Status, plain-English detail and locality for ``s``."""
        manifest = self.manifest(cap_id)
        if cap_id == "gate" and s.use_chat:
            chat = self.evaluate("chat", self.stored("chat"))
            if chat.status == "not_configured":
                return Evaluation(
                    "not_configured", "Uses the chat model, which is not set up yet.", None
                )
            if s.last_test is not None and not s.last_test.ok:
                return Evaluation(
                    "error", f"Last test failed: {s.last_test.message}", chat.locality
                )
            return Evaluation(chat.status, f"Uses the chat model. {chat.detail}", chat.locality)
        if "enabled" in manifest.ui_fields and not s.enabled:
            if cap_id == "grounding":
                return Evaluation("disabled", "Off. Answers are marked Unverified.", "local")
            if manifest.hidden:
                return Evaluation("disabled", "Not used yet.", None)
            return Evaluation("disabled", "Off.", None)
        if cap_id == "web_search":
            return self._evaluate_search(s)
        if self._uses_endpoint(cap_id, s):
            return self._evaluate_endpoint(cap_id, s)
        return self._evaluate_bundled(cap_id, s)

    def _failed_test(self, s: CapabilitySettings) -> str | None:
        if s.last_test is not None and not s.last_test.ok:
            return f"Last test failed: {s.last_test.message}"
        return None

    def _evaluate_bundled(self, cap_id: str, s: CapabilitySettings) -> Evaluation:
        info = self._providers.info(_BUNDLED_KINDS[cap_id])
        if not info.present:
            return Evaluation("error", "Model files are missing from this install.", "local")
        if not self._providers.runtime_available(_BUNDLED_KINDS[cap_id]):
            return Evaluation("error", "The model runtime is not installed in this build.", "local")
        failed = self._failed_test(s)
        if failed:
            return Evaluation("error", failed, "local")
        suffix = ", fallback model" if info.fallback else ""
        return Evaluation("ready", f"Runs on this computer ({info.model_id}{suffix}).", "local")

    def _evaluate_endpoint(self, cap_id: str, s: CapabilitySettings) -> Evaluation:
        missing = self.missing_fields(cap_id, s)
        preset = self.effective_preset(cap_id, s)
        if missing == ["preset"]:
            return Evaluation("not_configured", "Choose a provider.", None)
        if missing:
            azure = preset is not None and preset.adapter == "azure_openai"
            phrases = [_missing_phrase(name, azure) for name in missing]
            joined = " and ".join(", ".join(phrases).rsplit(", ", 1))
            return Evaluation("not_configured", f"Add the {joined}.", None)
        if preset is not None and preset.adapter == "mock":
            locality: Locality | None = "local"
            ready = "Ready. Test mode: answers are simulated."
        else:
            locality = locality_for_url(s.base_url)
            host = urlsplit(s.base_url or "").hostname or ""
            ready = f"Ready. Uses {s.model} at {host}."
        failed = self._failed_test(s)
        if failed:
            return Evaluation("error", failed, locality)
        return Evaluation("ready", ready, locality)

    def _evaluate_search(self, s: CapabilitySettings) -> Evaluation:
        provider = s.provider or "searxng"
        if provider == "searxng" and s.base_url:
            host = urlsplit(s.base_url).hostname or ""
            evaluation = Evaluation(
                "ready",
                f"Uses SearXNG at {host}. Your search words are passed on to public "
                "search engines.",
                locality_for_url(s.base_url),
            )
        elif provider == "searxng":
            evaluation = Evaluation(
                "ready",
                "Uses the built-in SearXNG. Your search words are passed on to public "
                "search engines.",
                "local",
            )
        elif provider == "mock":
            if not self._paths.mock_llm:
                return Evaluation("not_configured", "Choose a search service.", None)
            evaluation = Evaluation("ready", "Test mode: fixed search results.", "local")
        else:
            label = "Brave Search" if provider == "brave" else "Tavily"
            if not s.api_key:
                return Evaluation("not_configured", f"Add your {label} API key.", None)
            evaluation = Evaluation("ready", f"Ready. Uses {label}.", "remote")
        failed = self._failed_test(s)
        if failed:
            return Evaluation("error", failed, evaluation.locality)
        return evaluation

    # --- updates ---------------------------------------------------------------------------

    def merge(self, cap_id: str, update: CapabilityConfigUpdate) -> CapabilitySettings:
        """Apply ``update`` to the saved config and return the result (nothing is saved)."""
        manifest = self.manifest(cap_id)
        stored = self.stored(cap_id)
        merged = stored.model_copy(deep=True)
        given = update.model_fields_set
        allowed = set(manifest.ui_fields) | ({"preset"} if manifest.presets else set())
        for name in given:
            if getattr(update, name) is not None and name not in allowed:
                raise ConfigError(f"“{name}” does not apply to {manifest.label}.")

        if "enabled" in given and update.enabled is not None:
            merged.enabled = update.enabled
        if "use_chat" in given and update.use_chat is not None:
            merged.use_chat = update.use_chat
        if "code_judge" in given and update.code_judge is not None:
            merged.code_judge = update.code_judge
        if "mode" in given and update.mode is not None:
            if update.mode not in ("bundled", "endpoint"):
                raise ConfigError("Choose either the built-in model or an endpoint.")
            merged.mode = update.mode
        if "provider" in given and update.provider is not None:
            if update.provider not in {o.value for o in self.providers(cap_id)}:
                raise ConfigError("Choose one of the listed search services.")
            merged.provider = update.provider
        if "preset" in given:
            preset_id = _clean(update.preset)
            if preset_id is not None and self.preset(cap_id, preset_id) is None:
                raise ConfigError("Choose one of the listed providers.")
            if preset_id != stored.preset and "base_url" not in given:
                chosen = self.preset(cap_id, preset_id)
                merged.base_url = chosen.base_url if chosen else None
            merged.preset = preset_id
        if "base_url" in given:
            merged.base_url = _clean(update.base_url)
        if merged.base_url is not None and ("base_url" in given or "preset" in given):
            preset = self.effective_preset(cap_id, merged)
            adapter = "plain" if cap_id == "web_search" else None
            merged.base_url = normalise_base_url(
                merged.base_url, adapter or (preset.adapter if preset else "openai_compatible")
            )
        if "model" in given:
            merged.model = _clean(update.model)
        if "api_version" in given:
            merged.api_version = _clean(update.api_version)

        # A missing (or null) api_key keeps the saved key; "" clears it. A saved key is never
        # carried over to a different server or search service: it would leak to that host.
        if "api_key" in given and update.api_key is not None:
            merged.api_key = _clean(update.api_key)
        elif merged.api_key and (
            _origin(merged.base_url) != _origin(stored.base_url)
            or merged.provider != stored.provider
        ):
            merged.api_key = None
        return merged

    def update(self, cap_id: str, update: CapabilityConfigUpdate) -> CapabilityOut:
        """Merge and save ``update``; any change clears the previous test result."""
        merged = self.merge(cap_id, update)

        def apply(settings: Settings) -> None:
            previous = settings.capability(cap_id)
            if merged.model_dump(exclude={"last_test"}) != previous.model_dump(
                exclude={"last_test"}
            ):
                merged.last_test = None
            settings.capabilities[cap_id] = merged

        self._store.update(apply)
        return self.get(cap_id)

    def save_test(self, cap_id: str, result: TestResult) -> None:
        """Store ``result`` as the saved config's last test."""

        def apply(settings: Settings) -> None:
            current = settings.capability(cap_id)
            current.last_test = result
            settings.capabilities[cap_id] = current

        self._store.update(apply)

    # --- resolution ------------------------------------------------------------------------

    def effective(
        self, cap_id: str, draft: CapabilityConfigUpdate | None = None
    ) -> CapabilitySettings:
        """Saved config merged with an optional draft."""
        if draft is None or draft.is_empty():
            return self.stored(cap_id)
        return self.merge(cap_id, draft)

    def endpoint(
        self,
        cap_id: str,
        draft: CapabilityConfigUpdate | None = None,
        *,
        require_model: bool = True,
    ) -> EndpointConfig:
        """Resolve the endpoint to call for ``cap_id`` (gate with use_chat → chat's endpoint).

        ``require_model=False`` is for listing models, which is how users find the name.
        """
        settings = self.effective(cap_id, draft)
        source_id = cap_id
        if cap_id == "gate" and settings.use_chat:
            source_id = "chat"
            settings = self.stored("chat")
        if not self._uses_endpoint(source_id, settings):
            raise ConfigError(f"{self.manifest(cap_id).label} does not use an endpoint.")
        missing = [
            name
            for name in self.missing_fields(source_id, settings)
            if require_model or name != "model"
        ]
        if missing:
            evaluation = self._evaluate_endpoint(source_id, settings)
            raise ConfigError(evaluation.detail)
        preset = self.effective_preset(source_id, settings)
        assert preset is not None
        return EndpointConfig(
            capability=cap_id,
            adapter=preset.adapter,
            base_url=settings.base_url,
            model=settings.model,
            api_version=settings.api_version,
            api_key=settings.api_key,
        )


__all__ = [
    "LOCAL_HOSTS",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "ConfigError",
    "EndpointConfig",
    "Evaluation",
    "locality_for_url",
    "normalise_base_url",
]
