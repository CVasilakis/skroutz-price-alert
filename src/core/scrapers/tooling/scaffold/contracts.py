"""Validated contracts shared by plugin scaffold entry points."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.scrapers.domain import normalize_domain
from core.scrapers.framework.intervals import SUPPORTED_INTERVALS
from core.scrapers.framework.naming import (
    FRAMEWORK_ITEM_KEYS,
    RESERVED_PLUGIN_NAMES,
    SNAKE_CASE_KEY,
)
from core.scrapers.framework.settings import framework_setting_specs

ResultType = Literal["price", "listing"]
Transport = Literal["bare", "http"]
VALUE_TYPES = (
    "text",
    "integer",
    "number",
    "nonnegative-number",
    "boolean",
    "text-list",
)
_REQUIRED = object()
PUBLIC_COMMAND_NAMES = frozenset(
    {
        "run",
        "ping",
        "status",
        "install",
        "enable",
        "disable",
        "stop",
        "schedule",
        "update",
        "uninstall",
    }
)


@dataclass(frozen=True)
class CustomValueSpec:
    """One custom item field or setting the contributor asked the scaffold to declare.

    A request for generated source, not a runtime declaration: it carries what the
    generator needs to emit a typed :class:`~core.scrapers.api.ItemField` or
    ``SettingSpec`` plus a matching example-config entry.
    """

    key: str
    """The snake_case key, validated against the same rules the compiler enforces."""

    value_type: str
    """Which decoder to generate, from the wizard's supported vocabulary."""

    example: object
    """The value written into ``config.example.json``, so the example is runnable."""

    default: object = _REQUIRED
    """The generated default; the sentinel makes the declaration required instead."""

    sensitive: bool = False
    """Settings only: generate ``sensitive=True`` so panels redact the value."""

    @property
    def required(self) -> bool:
        """Whether the generated declaration will omit a default."""
        return self.default is _REQUIRED


@dataclass(frozen=True)
class ScaffoldRequest:
    """Everything needed to generate one plugin package, fully validated.

    The single value both entry points produce — the guided wizard and the
    non-interactive CLI — so the two cannot drift into generating different
    packages from equivalent input. Nothing is written until a request validates.
    """

    target: str
    """The package directory name, which becomes every managed path and the CLI flag."""

    display_name: str
    """The store's human-readable name for panels and notifications."""

    domains: tuple[str, ...]
    """Hosts the generated ``UrlField`` will accept."""

    url_prefix: str
    """Path prefix the generated URL predicate will require."""
    result_type: ResultType = "price"
    """Whether the generated client returns a single price or a listing."""

    default_interval: str = "1h"
    """The plugin's canonical cadence; must already be a supported interval."""

    transport: Transport = "bare"
    """Subclass the shared HTTP helper, or a bare client with no transport."""

    item_fields: tuple[CustomValueSpec, ...] = ()
    """Custom item fields to declare, beyond the framework's own and the URL."""

    settings: tuple[CustomValueSpec, ...] = ()
    """Custom settings to declare, beyond the framework's own."""

    dependencies: tuple[str, ...] = ()
    """Private requirements written to the package's own ``requirements.txt``."""

    include_tests: bool = True
    """Also generate ``tests/plugins/<target>/``; optional but recommended."""


@dataclass(frozen=True)
class ScaffoldResult:
    """Where a successful scaffold wrote, for reporting back to the contributor."""

    source: Path
    """The created plugin package."""

    tests: Path | None
    """The created test package, or ``None`` when tests were declined."""


def safe_display_name(value: str) -> str:
    """Validate a display name against the same rules compilation will apply.

    Rejecting control characters here rather than at compile time means the
    contributor is told while answering, not after a package exists.
    """
    result = value.strip()
    if not result or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("display name must be nonblank and contain no control characters")
    return result


def target_name(value: str) -> str:
    """Validate a target name, explaining the fix rather than only refusing.

    The name determines the package, CLI flag, config, state, logs, and unit names,
    so it is checked against the framework's snake_case rule and its reserved names
    before anything is generated.
    """
    result = value.strip()
    if not result:
        raise ValueError("target name must not be empty")
    lowercase = result.lower()
    if lowercase != result and SNAKE_CASE_KEY.fullmatch(lowercase) is not None:
        raise ValueError(
            f"target name must use lowercase letters; try {lowercase!r} instead of {result!r}"
        )
    if not "a" <= result[0] <= "z":
        raise ValueError("target name must begin with a lowercase letter")
    if SNAKE_CASE_KEY.fullmatch(result) is None:
        raise ValueError(
            "target name may contain only lowercase letters, digits, and underscores; "
            "use underscores between words"
        )
    if result in RESERVED_PLUGIN_NAMES:
        raise ValueError(f"target name {result!r} is reserved; choose a store-specific name")
    if result in PUBLIC_COMMAND_NAMES:
        raise ValueError(
            f"target name {result!r} matches a Scrooge Alert command; "
            "choose the store or service name instead"
        )
    return result


def url_prefix(value: str) -> str:
    """Validate the path prefix the generated URL predicate will require.

    A path only: a query or fragment here would generate a predicate that can never
    match, since the framework hands the predicate a parsed URL whose path is
    separate from both.
    """
    result = value.strip()
    if not result.startswith("/") or any(char in result for char in "?#"):
        raise ValueError("URL prefix must start with '/' and contain no query or fragment")
    if re.search(r"\s", result):
        raise ValueError("URL prefix must not contain whitespace")
    return result if result.endswith("/") else result + "/"


def decode_value(value_type: str, raw: object) -> object:
    """Decode one answer using the same rules the generated decoder will enforce.

    Deliberately mirrors the generated code so the wizard cannot accept an example
    or default that the finished plugin would then reject at compile time.
    """
    if value_type == "text":
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("must be a nonblank string")
        return raw.strip()
    if value_type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("must be an integer")
        return raw
    if value_type in {"number", "nonnegative-number"}:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("must be a number")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("must be finite")
        if value_type == "nonnegative-number" and value < 0:
            raise ValueError("must be non-negative")
        return value
    if value_type == "boolean":
        if not isinstance(raw, bool):
            raise ValueError("must be a boolean")
        return raw
    if value_type == "text-list":
        if not isinstance(raw, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in raw
        ):
            raise ValueError("must be an array of nonblank strings")
        return tuple(value.strip() for value in raw)
    raise ValueError(f"type must be one of {', '.join(VALUE_TYPES)}")


def _validate_specs(
    specs: tuple[CustomValueSpec, ...], *, kind: str, reserved: frozenset[str]
) -> tuple[CustomValueSpec, ...]:
    result: list[CustomValueSpec] = []
    seen: set[str] = set()
    for spec in specs:
        key = spec.key.strip()
        if SNAKE_CASE_KEY.fullmatch(key) is None or key in reserved:
            raise ValueError(f"{kind} key {key!r} must be a non-reserved snake_case name")
        if key in seen:
            raise ValueError(f"duplicate {kind} key {key!r}")
        seen.add(key)
        try:
            example = decode_value(spec.value_type, spec.example)
            default = _REQUIRED if spec.required else decode_value(spec.value_type, spec.default)
        except ValueError as exc:
            raise ValueError(f"{kind} {key!r} {exc}") from exc
        result.append(
            CustomValueSpec(
                key=key,
                value_type=spec.value_type,
                example=example,
                default=default,
                sensitive=spec.sensitive,
            )
        )
    return tuple(result)


def _safe_dependencies(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        requirement = value.strip()
        if not requirement or any(ord(char) < 32 or ord(char) == 127 for char in requirement):
            raise ValueError("dependencies must be nonblank single-line requirement strings")
        if requirement not in result:
            result.append(requirement)
    return tuple(result)


def validate_request(request: ScaffoldRequest) -> ScaffoldRequest:
    """Validate and normalize one scaffold request, or raise before anything is written.

    The single gate both entry points pass through. It applies the framework's own
    rules — snake_case keys, reserved names, host-only domains, supported intervals,
    canonical defaults — so a generated package is valid by construction rather than
    only being found invalid later by the compiler.

    Returns:
        The normalized request to generate from.

    Raises:
        ValueError: Any answer is unusable, with wording that names the fix.
    """
    target = target_name(request.target)
    if not request.domains:
        raise ValueError("at least one domain is required")
    domains: list[str] = []
    for raw_domain in request.domains:
        domain = normalize_domain(raw_domain)
        if domain not in domains:
            domains.append(domain)
    if request.result_type not in {"price", "listing"}:
        raise ValueError("result type must be 'price' or 'listing'")
    if request.transport not in {"bare", "http"}:
        raise ValueError("transport must be 'bare' or 'http'")
    if request.default_interval not in SUPPORTED_INTERVALS:
        raise ValueError("default interval must be one of " + ", ".join(SUPPORTED_INTERVALS))
    framework_setting_keys = frozenset(
        spec.key for spec in framework_setting_specs(request.default_interval)
    )
    return ScaffoldRequest(
        target=target,
        display_name=safe_display_name(request.display_name),
        domains=tuple(domains),
        url_prefix=url_prefix(request.url_prefix),
        result_type=request.result_type,
        default_interval=request.default_interval,
        transport=request.transport,
        item_fields=_validate_specs(
            request.item_fields,
            kind="item field",
            reserved=FRAMEWORK_ITEM_KEYS | frozenset({"url"}),
        ),
        settings=_validate_specs(
            request.settings,
            kind="setting",
            reserved=framework_setting_keys,
        ),
        dependencies=_safe_dependencies(request.dependencies),
        include_tests=request.include_tests,
    )


def reject_json_constant(value: str) -> object:
    """Reject ``NaN`` and infinities, which Python accepts but strict JSON does not.

    Generated examples must be loadable by any JSON parser, not only Python's.
    """
    raise ValueError(f"{value} is not permitted by strict JSON")


def parse_strict_json(raw: str) -> object:
    """Decode standards-compliant JSON, rejecting Python's non-finite extensions."""
    try:
        return json.loads(raw, parse_constant=reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(exc.msg) from exc


def json_value(raw: str, *, context: str) -> object:
    """Parse one typed answer as strict JSON, naming the field in any failure.

    Args:
        raw: The literal the contributor typed.
        context: Which answer this is, so the error points at the right question.
    """
    try:
        return parse_strict_json(raw)
    except ValueError as exc:
        raise ValueError(f"{context} must be valid JSON: {exc}") from exc


__all__ = [
    "CustomValueSpec",
    "PUBLIC_COMMAND_NAMES",
    "ResultType",
    "ScaffoldRequest",
    "ScaffoldResult",
    "Transport",
    "VALUE_TYPES",
    "decode_value",
    "json_value",
    "parse_strict_json",
    "safe_display_name",
    "target_name",
    "url_prefix",
    "validate_request",
]
