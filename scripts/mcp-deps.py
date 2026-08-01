from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
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
_XATTR_NAME = re.compile(r"[A-Za-z0-9._-]+\Z")
_MAX_REGISTRY_RESPONSE = 2 * 1024 * 1024


@dataclass(frozen=True)
class DependencySpec:
    server: str
    ecosystem: str
    package: str
    version: str | None
    pin_state: str
    argument_index: int


@dataclass(frozen=True)
class FileMetadata:
    stat: os.stat_result
    xattrs: tuple[str, ...]


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


def _target_is_available(
    spec: DependencySpec, version: str, fetch_json: Callable[[str], object]
) -> bool:
    try:
        payload = fetch_json(_registry_url(spec))
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False
    if spec.ecosystem == "pypi":
        releases = payload.get("releases")
        files = releases.get(version) if isinstance(releases, dict) else None
        return isinstance(files, list) and any(
            isinstance(file, dict) and file.get("yanked") is False
            for file in files
        )
    if spec.ecosystem == "npm":
        versions = payload.get("versions")
        selected = versions.get(version) if isinstance(versions, dict) else None
        return isinstance(selected, dict) and not bool(selected.get("deprecated"))
    return False


def _regular_file_metadata(path: Path) -> FileMetadata:
    try:
        source = path.lstat()
    except OSError:
        raise DependencyError("MCP dependency config is unavailable") from None
    if stat.S_ISLNK(source.st_mode):
        raise DependencyError("MCP dependency config cannot be a symlink")
    if not stat.S_ISREG(source.st_mode) or source.st_nlink != 1:
        raise DependencyError("MCP dependency config is not a regular file")
    if getattr(source, "st_flags", 0):
        raise DependencyError("MCP dependency config has unsupported metadata")
    return FileMetadata(source, _xattr_names(path))


def _xattr_names(path: Path) -> tuple[str, ...]:
    if hasattr(os, "listxattr"):
        try:
            return tuple(sorted(os.listxattr(path, follow_symlinks=False)))
        except OSError:
            raise DependencyError("MCP dependency config metadata is unsupported") from None
    if sys.platform != "darwin":
        raise DependencyError("MCP dependency config metadata is unsupported")

    try:
        result = subprocess.run(
            ["/usr/bin/xattr", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        raise DependencyError("MCP dependency config metadata is unsupported") from None
    if result.returncode != 0:
        raise DependencyError("MCP dependency config metadata is unsupported")
    try:
        names = tuple(sorted(result.stdout.decode("utf-8").splitlines()))
    except UnicodeDecodeError:
        raise DependencyError("MCP dependency config metadata is unsupported") from None
    if not all(_XATTR_NAME.fullmatch(name) for name in names):
        raise DependencyError("MCP dependency config has unsupported metadata")
    if _has_macos_acl(path):
        raise DependencyError("MCP dependency config has unsupported metadata")
    return names


def _has_macos_acl(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["/bin/ls", "-le", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return True
    return result.returncode != 0 or len(result.stdout.splitlines()) > 1


def _copy_xattrs(source: Path, target: Path, names: tuple[str, ...]) -> None:
    try:
        target_names = _xattr_names(target)
        for name in target_names:
            if name not in names:
                _remove_xattr(target, name)
        for name in names:
            value = _read_xattr(source, name)
            _write_xattr(target, name, value)
            if _read_xattr(target, name) != value:
                raise DependencyError("MCP dependency file metadata cannot be preserved")
        if _xattr_names(target) != names:
            raise DependencyError("MCP dependency file metadata cannot be preserved")
    except DependencyError:
        raise
    except OSError:
        raise DependencyError("MCP dependency file metadata cannot be preserved") from None


def _read_xattr(path: Path, name: str) -> bytes:
    if hasattr(os, "getxattr"):
        return os.getxattr(path, name, follow_symlinks=False)
    result = subprocess.run(
        ["/usr/bin/xattr", "-px", name, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    value = b"".join(result.stdout.split())
    if result.returncode != 0 or not re.fullmatch(rb"[0-9A-Fa-f]*", value):
        raise DependencyError("MCP dependency file metadata cannot be preserved")
    return value


def _write_xattr(path: Path, name: str, value: bytes) -> None:
    if hasattr(os, "setxattr"):
        os.setxattr(path, name, value, follow_symlinks=False)
        return
    result = subprocess.run(
        ["/usr/bin/xattr", "-wx", name, value.decode("ascii"), str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise DependencyError("MCP dependency file metadata cannot be preserved")


def _remove_xattr(path: Path, name: str) -> None:
    if hasattr(os, "removexattr"):
        os.removexattr(path, name, follow_symlinks=False)
        return
    result = subprocess.run(
        ["/usr/bin/xattr", "-d", name, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise DependencyError("MCP dependency file metadata cannot be preserved")


def _same_source(left: FileMetadata, right: FileMetadata) -> bool:
    return (
        left.stat.st_dev,
        left.stat.st_ino,
        left.stat.st_size,
        left.stat.st_mtime_ns,
        left.stat.st_ctime_ns,
        left.stat.st_mode,
        left.stat.st_uid,
        left.stat.st_gid,
        left.xattrs,
    ) == (
        right.stat.st_dev,
        right.stat.st_ino,
        right.stat.st_size,
        right.stat.st_mtime_ns,
        right.stat.st_ctime_ns,
        right.stat.st_mode,
        right.stat.st_uid,
        right.stat.st_gid,
        right.xattrs,
    )


def _preserve_regular_file_metadata(
    descriptor: int, source: FileMetadata
) -> None:
    try:
        temporary = os.fstat(descriptor)
        if (temporary.st_uid, temporary.st_gid) != (
            source.stat.st_uid,
            source.stat.st_gid,
        ):
            os.fchown(descriptor, source.stat.st_uid, source.stat.st_gid)
        os.fchmod(descriptor, stat.S_IMODE(source.stat.st_mode))
        temporary = os.fstat(descriptor)
    except OSError:
        raise DependencyError("MCP dependency file metadata cannot be preserved") from None
    if (temporary.st_uid, temporary.st_gid) != (
        source.stat.st_uid,
        source.stat.st_gid,
    ) or (
        stat.S_IMODE(temporary.st_mode) != stat.S_IMODE(source.stat.st_mode)
    ):
        raise DependencyError("MCP dependency file metadata cannot be preserved")


def _atomic_write(path: Path, content: bytes, source: FileMetadata) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        _preserve_regular_file_metadata(descriptor, source)
        _copy_xattrs(path, Path(temporary_name), source.xattrs)
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def pin_dependency(
    hub: Path,
    server: str,
    version: str,
    *,
    apply: bool,
    fetch_json: Callable[[str], object],
) -> str:
    if not isinstance(version, str) or not _NUMERIC_VERSION.fullmatch(version):
        raise DependencyError("invalid MCP dependency target version")

    config_path = hub / "mcp" / "shared-servers.json"
    source = _regular_file_metadata(config_path)
    spec = next(
        (item for item in discover_dependencies(hub) if item.server == server),
        None,
    )
    if spec is None:
        raise DependencyError("unknown MCP dependency server")
    if not _target_is_available(spec, version, fetch_json):
        raise DependencyError("MCP dependency target is unavailable")

    original = config_path.read_bytes()
    document = json.loads(original)
    config = document.get("mcpServers", {}).get(server)
    if not isinstance(config, dict):
        raise DependencyError("unknown MCP dependency server")
    args = config.get("args")
    if not isinstance(args, list) or len(args) <= spec.argument_index:
        raise DependencyError("invalid MCP dependency argument")
    old_argument = args[spec.argument_index]
    if not isinstance(old_argument, str):
        raise DependencyError("invalid MCP dependency argument")

    separator = "==" if spec.ecosystem == "pypi" else "@"
    new_argument = f"{spec.package}{separator}{version}"
    old_token = json.dumps(old_argument).encode("utf-8")
    if original.count(old_token) != 1:
        raise DependencyError("MCP dependency argument is not uniquely replaceable")
    proposed = original.replace(old_token, json.dumps(new_argument).encode("utf-8"), 1)

    if apply:
        if not _same_source(source, _regular_file_metadata(config_path)):
            raise DependencyError("MCP dependency config changed during pinning")
        _atomic_write(config_path, proposed, source)
    return proposed.decode("utf-8")
