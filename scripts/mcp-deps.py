from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


_NUMERIC_VERSION = re.compile(r"\d+(?:\.\d+)*\Z")
_NPM_PACKAGE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*|@[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)\Z"
)
_PYPI_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_REGISTRY_RESPONSE = 2 * 1024 * 1024


@dataclass(frozen=True)
class DependencySpec:
    server: str
    ecosystem: str
    package: str
    version: str | None
    pin_state: str
    argument_index: int


class DependencyError(ValueError):
    pass


def default_fetch_json(url: str, opener=None) -> object:
    open_url = urlopen if opener is None else opener
    try:
        response = open_url(url, timeout=8)
    except HTTPError as error:
        try:
            error.close()
        except Exception:
            pass
        raise DependencyError("registry request failed") from None
    except Exception:
        raise DependencyError("registry request failed") from None

    try:
        declared_length = response.headers.get("Content-Length")
        try:
            declared_size = int(declared_length)
        except (TypeError, ValueError):
            declared_size = None
        if (
            declared_size is not None
            and declared_size > _MAX_REGISTRY_RESPONSE
        ):
            raise DependencyError("registry response rejected")

        payload = response.read(_MAX_REGISTRY_RESPONSE + 1)
        if len(payload) > _MAX_REGISTRY_RESPONSE:
            raise DependencyError("registry response rejected")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DependencyError("invalid registry response") from None
    except DependencyError:
        raise
    except Exception:
        raise DependencyError("registry request failed") from None
    finally:
        try:
            response.close()
        except Exception:
            pass


def _malformed(server: str, launcher: str) -> DependencyError:
    return DependencyError(
        f"{server}: invalid {launcher} dependency argument at position 0"
    )


def _valid_package(package: str, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.fullmatch(package))


def _parse_npm(server: str, argument: object) -> DependencySpec:
    if not isinstance(argument, str) or argument.startswith("-"):
        raise _malformed(server, "npx")

    if argument.startswith("@"):
        version_separator = argument.rfind("@")
        if version_separator == 0:
            package, version = argument, None
        else:
            package, version = argument[:version_separator], argument[version_separator + 1 :]
    elif "@" in argument:
        package, version = argument.rsplit("@", 1)
    else:
        package, version = argument, None

    if not _valid_package(package, _NPM_PACKAGE):
        raise _malformed(server, "npx")
    if version is None:
        pin_state = "unversioned"
    elif version == "latest":
        version = None
        pin_state = "latest"
    elif _NUMERIC_VERSION.fullmatch(version):
        pin_state = "exact"
    else:
        raise _malformed(server, "npx")
    return DependencySpec(server, "npm", package, version, pin_state, 0)


def _parse_pypi(server: str, argument: object) -> DependencySpec:
    if not isinstance(argument, str) or argument.startswith("-"):
        raise _malformed(server, "uvx")

    if "==" not in argument:
        if not _valid_package(argument, _PYPI_PACKAGE):
            raise _malformed(server, "uvx")
        return DependencySpec(server, "pypi", argument, None, "unversioned", 0)

    package, version = argument.split("==", 1)
    if not _valid_package(package, _PYPI_PACKAGE) or not _NUMERIC_VERSION.fullmatch(version):
        raise _malformed(server, "uvx")
    return DependencySpec(server, "pypi", package, version, "exact", 0)


def discover_dependencies(hub: Path) -> list[DependencySpec]:
    config_path = hub / "mcp" / "shared-servers.json"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    servers = document.get("mcpServers", {})
    dependencies = []

    for server, config in servers.items():
        command = config.get("command") if isinstance(config, dict) else None
        if not isinstance(command, str):
            continue
        launcher = Path(command).name
        if launcher not in {"npx", "uvx"}:
            continue

        args = config.get("args", [])
        if not isinstance(args, list) or not args:
            raise _malformed(server, launcher)
        parser = _parse_npm if launcher == "npx" else _parse_pypi
        dependencies.append(parser(server, args[0]))

    return dependencies


def _clean_registry_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _CONTROL_CHARACTERS.sub("", value)


def _registry_url(spec: DependencySpec) -> str:
    package = _clean_registry_text(spec.package) or ""
    encoded_package = quote(package, safe="")
    if spec.ecosystem == "pypi":
        return f"https://pypi.org/pypi/{encoded_package}/json"
    return f"https://registry.npmjs.org/{encoded_package}"


def _pypi_metadata(payload: object) -> tuple[str | None, bool]:
    if not isinstance(payload, dict):
        return None, False
    info = payload.get("info")
    releases = payload.get("releases")
    if not isinstance(info, dict) or not isinstance(releases, dict):
        return None, False
    raw_latest = info.get("version")
    latest = _clean_registry_text(raw_latest)
    if latest is None or not _NUMERIC_VERSION.fullmatch(latest):
        return None, False
    files = releases.get(raw_latest)
    if not isinstance(files, list) or not any(
        isinstance(file, dict) and file.get("yanked") is False for file in files
    ):
        return None, False
    return latest, False


def _npm_metadata(payload: object) -> tuple[str | None, bool]:
    if not isinstance(payload, dict):
        return None, False
    tags = payload.get("dist-tags")
    versions = payload.get("versions")
    if not isinstance(tags, dict) or not isinstance(versions, dict):
        return None, False
    raw_latest = tags.get("latest")
    latest = _clean_registry_text(raw_latest)
    if latest is None or not _NUMERIC_VERSION.fullmatch(latest):
        return None, False
    selected = versions.get(raw_latest)
    if not isinstance(selected, dict):
        return None, False
    return latest, bool(selected.get("deprecated"))


def _numeric_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _compare_versions(current: str, latest: str) -> int:
    left = list(_numeric_version(current))
    right = list(_numeric_version(latest))
    width = max(len(left), len(right))
    left.extend([0] * (width - len(left)))
    right.extend([0] * (width - len(right)))
    return (left > right) - (left < right)


def check_dependencies(
    specs: list[DependencySpec], fetch_json: Callable[[str], object]
) -> list[dict[str, object]]:
    results = []
    for spec in specs:
        package = _clean_registry_text(spec.package) or ""
        current = _clean_registry_text(spec.version)
        metadata_url = _registry_url(spec)
        latest = None
        deprecated = False
        try:
            payload = fetch_json(metadata_url)
            if spec.ecosystem == "pypi":
                latest, deprecated = _pypi_metadata(payload)
            elif spec.ecosystem == "npm":
                latest, deprecated = _npm_metadata(payload)
        except Exception:
            pass

        if latest is None:
            status = "unknown"
        elif deprecated:
            status = "deprecated"
        elif spec.pin_state in {"latest", "unversioned"}:
            status = "unpinned"
        elif current is None or not _NUMERIC_VERSION.fullmatch(current):
            status = "unknown"
        else:
            comparison = _compare_versions(current, latest)
            if comparison < 0:
                status = "update-available"
            elif comparison > 0:
                status = "ahead"
            else:
                status = "current"

        results.append(
            {
                "server": spec.server,
                "ecosystem": spec.ecosystem,
                "package": package,
                "current": current,
                "latest": latest,
                "pin_state": spec.pin_state,
                "status": status,
                "deprecated": deprecated,
                "metadata_url": metadata_url,
            }
        )
    return results
