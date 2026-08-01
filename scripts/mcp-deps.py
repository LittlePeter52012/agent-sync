from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_NUMERIC_VERSION = re.compile(r"\d+(?:\.\d+)*\Z")
_NPM_PACKAGE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*|@[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)\Z"
)
_PYPI_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


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
