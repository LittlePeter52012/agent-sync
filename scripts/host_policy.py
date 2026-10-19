"""Host-local capability overrides that never leave this machine.

``~/.config/agent-sync/host-policy.json`` lets one host turn a Hub capability
off for specific targets, for example when the product plugin the Hub selects
is not installed on that host and falling back to the Hub MCP is unwanted. The
file lives outside both the Hub and the tool checkout, so ``agent-sync deploy``
never ships, replaces or backs it up::

    {
      "schema_version": 1,
      "capability_ownership": {
        "capabilities": {
          "<capability>": {
            "targets": {
              "<target>": {"selection": "disabled", "reason": "<why>"}
            }
          }
        }
      }
    }

Only ``"selection": "disabled"`` is supported, a non-empty ``reason`` is
required, and every capability must exist in the Hub policy. Anything else is
an error, so a typo cannot quietly change ownership.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

HOST_POLICY_RELATIVE = Path(".config") / "agent-sync" / "host-policy.json"
SCHEMA_VERSION = 1
SUPPORTED_SELECTIONS = frozenset({"disabled"})
_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]*(?::[A-Za-z0-9._ -]+)?$")


class HostPolicyError(ValueError):
    """The host policy file is unreadable or does not follow the schema."""


@dataclass(frozen=True)
class HostOverride:
    selection: str
    reason: str


def host_policy_path(home: Path) -> Path:
    return home.expanduser() / HOST_POLICY_RELATIVE


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HostPolicyError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _object(value: Any, where: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HostPolicyError(f"{where} must be an object")
    unknown = sorted(set(value) - keys)
    if unknown:
        raise HostPolicyError(f"{where} has unsupported keys: {', '.join(unknown)}")
    return value


def _keyed_objects(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise HostPolicyError(f"{where} must be a non-empty object")
    for key in value:
        if not _KEY.fullmatch(key):
            raise HostPolicyError(f"{where} has an invalid key {key!r}")
    return value


def parse_host_policy(
    text: str,
    known_capabilities: Iterable[str] | None = None,
) -> dict[tuple[str, str], HostOverride]:
    """``{(capability, target): override}`` from the file contents."""
    try:
        document = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise HostPolicyError(
            f"invalid JSON at line {error.lineno} column {error.colno}"
        ) from None
    root = _object(document, "root", {"schema_version", "capability_ownership"})
    version = root.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise HostPolicyError(f"schema_version must be {SCHEMA_VERSION}")
    ownership = _object(
        root.get("capability_ownership"), "capability_ownership", {"capabilities"}
    )
    capabilities = _keyed_objects(
        ownership.get("capabilities"), "capability_ownership.capabilities"
    )
    known = None if known_capabilities is None else set(known_capabilities)
    overrides: dict[tuple[str, str], HostOverride] = {}
    for capability, raw_capability in capabilities.items():
        where = f"capabilities.{capability}"
        if known is not None and capability not in known:
            raise HostPolicyError(f"{where} is not a capability in the Hub policy")
        entry = _object(raw_capability, where, {"targets"})
        targets = _keyed_objects(entry.get("targets"), f"{where}.targets")
        for target, raw_target in targets.items():
            target_where = f"{where}.targets.{target}"
            policy = _object(raw_target, target_where, {"selection", "reason"})
            selection = policy.get("selection")
            if selection not in SUPPORTED_SELECTIONS:
                raise HostPolicyError(
                    f"{target_where}.selection must be one of: "
                    + ", ".join(sorted(SUPPORTED_SELECTIONS))
                )
            reason = policy.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise HostPolicyError(f"{target_where}.reason must be a non-empty string")
            overrides[(capability, target)] = HostOverride(selection, reason.strip())
    return overrides


def load_host_policy(
    home: Path,
    known_capabilities: Iterable[str] | None = None,
) -> dict[tuple[str, str], HostOverride]:
    """Overrides from this host's policy file; ``{}`` when there is none."""
    path = host_policy_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as error:
        raise HostPolicyError(f"host policy {path} is unreadable: {error}") from None
    try:
        return parse_host_policy(text, known_capabilities)
    except HostPolicyError as error:
        raise HostPolicyError(f"host policy {path}: {error}") from None
