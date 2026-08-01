from __future__ import annotations

import argparse
import json
import importlib.util
import math
import os
import queue
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen
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
_MAX_CHILD_OUTPUT = 256 * 1024
_MCP_PROTOCOL_VERSION = "2025-06-18"
_SHUTDOWN_GRACE = 0.1
_SAFE_SERVER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_OS_MANAGED_XATTRS = frozenset({"com.apple.provenance"})
_CACHE_TTL = 86400
_CACHE_KEYS = frozenset({"checked_at", "dependencies"})
_CACHE_RECORD_KEYS = frozenset(
    {
        "server",
        "ecosystem",
        "package",
        "current",
        "latest",
        "pin_state",
        "status",
        "deprecated",
        "metadata_url",
    }
)
_SENSITIVE_CACHE_KEY = re.compile(
    r"token|secret|password|header|oauth|vault|path", re.IGNORECASE
)
_STREAM_EOF = object()
_STREAM_LIMIT = object()


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


def _cache_path() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME")
    if not cache_root:
        home = os.environ.get("HOME")
        cache_root = str(Path(home) / ".cache") if home else str(Path.home() / ".cache")
    return Path(cache_root) / "agent-sync" / "mcp-deps.json"


def _has_sensitive_cache_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _SENSITIVE_CACHE_KEY.search(key):
                return True
            if _has_sensitive_cache_key(nested):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_cache_key(item) for item in value)
    return False


def _valid_cached_record(record: object) -> bool:
    if not isinstance(record, dict) or set(record) != _CACHE_RECORD_KEYS:
        return False
    server = record["server"]
    ecosystem = record["ecosystem"]
    package = record["package"]
    current = record["current"]
    latest = record["latest"]
    pin_state = record["pin_state"]
    status = record["status"]
    deprecated = record["deprecated"]
    metadata_url = record["metadata_url"]
    if not isinstance(server, str) or not _SAFE_SERVER_NAME.fullmatch(server):
        return False
    if ecosystem not in {"npm", "pypi"} or not isinstance(package, str):
        return False
    package_pattern = _NPM_PACKAGE if ecosystem == "npm" else _PYPI_PACKAGE
    if not package_pattern.fullmatch(package):
        return False
    if current is not None and (
        not isinstance(current, str) or not _NUMERIC_VERSION.fullmatch(current)
    ):
        return False
    if latest is not None and (
        not isinstance(latest, str) or not _NUMERIC_VERSION.fullmatch(latest)
    ):
        return False
    if pin_state not in {"exact", "latest", "unversioned"}:
        return False
    if not isinstance(deprecated, bool):
        return False
    spec = DependencySpec(server, ecosystem, package, current, pin_state, 0)
    if metadata_url != _registry_url(spec):
        return False

    if latest is None:
        expected_status = "unknown"
    elif deprecated:
        expected_status = "deprecated"
    elif pin_state in {"latest", "unversioned"}:
        expected_status = "unpinned"
    elif current is None:
        expected_status = "unknown"
    else:
        comparison = _compare_versions(current, latest)
        if comparison < 0:
            expected_status = "update-available"
        elif comparison > 0:
            expected_status = "ahead"
        else:
            expected_status = "current"
    return status == expected_status


def _validate_cache(payload: object, now: float) -> dict[str, object] | None:
    if _has_sensitive_cache_key(payload):
        return None
    if not isinstance(payload, dict) or set(payload) != _CACHE_KEYS:
        return None
    checked_at = payload["checked_at"]
    dependencies = payload["dependencies"]
    if (
        isinstance(checked_at, bool)
        or not isinstance(checked_at, (int, float))
        or not math.isfinite(checked_at)
        or checked_at > now
        or now - checked_at > _CACHE_TTL
        or not isinstance(dependencies, list)
        or not all(_valid_cached_record(record) for record in dependencies)
    ):
        return None
    return payload


def _read_cache(now: float | None = None) -> dict[str, object] | None:
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _validate_cache(payload, time.time() if now is None else now)


def _write_cache(dependencies: list[dict[str, object]], checked_at: float) -> None:
    cache_path = _cache_path()
    payload = {"checked_at": checked_at, "dependencies": dependencies}
    if _validate_cache(payload, checked_at) is None:
        raise DependencyError("MCP dependency cache update failed")
    descriptor = None
    temporary_name = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{cache_path.name}.", dir=cache_path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            descriptor = None
            os.fchmod(temporary.fileno(), 0o600)
            json.dump(payload, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, cache_path)
        temporary_name = None
    except Exception:
        raise DependencyError("MCP dependency cache update failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


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


def _regular_file_metadata(path: Path) -> os.stat_result:
    try:
        source = path.lstat()
    except OSError:
        raise DependencyError("MCP dependency config is unavailable") from None
    if stat.S_ISLNK(source.st_mode):
        raise DependencyError("MCP dependency config cannot be a symlink")
    if not stat.S_ISREG(source.st_mode) or source.st_nlink != 1:
        raise DependencyError("MCP dependency config is not a regular file")
    special_mode = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if getattr(source, "st_flags", 0) or source.st_mode & special_mode:
        raise DependencyError("MCP dependency config has unsupported metadata")
    has_xattrs = _has_xattrs(path)
    has_acl = sys.platform == "darwin" and _has_macos_acl(path)
    if has_xattrs or has_acl:
        raise DependencyError("MCP dependency config has unsupported metadata")
    return source


def _has_xattrs(path: Path) -> bool:
    if hasattr(os, "listxattr"):
        try:
            names = os.listxattr(path, follow_symlinks=False)
        except OSError:
            return True
        return any(
            not isinstance(name, str) or name not in _OS_MANAGED_XATTRS
            for name in names
        )
    if sys.platform != "darwin":
        return True

    try:
        result = subprocess.run(
            ["/usr/bin/xattr", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return True
    if result.returncode != 0:
        return True
    try:
        names = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return True
    return any(name not in _OS_MANAGED_XATTRS for name in names)


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


def _same_source(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        left.st_mode,
        left.st_uid,
        left.st_gid,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        right.st_mode,
        right.st_uid,
        right.st_gid,
    )


def _preserve_regular_file_metadata(
    descriptor: int, source: os.stat_result
) -> None:
    try:
        temporary = os.fstat(descriptor)
        if (temporary.st_uid, temporary.st_gid) != (
            source.st_uid,
            source.st_gid,
        ):
            os.fchown(descriptor, source.st_uid, source.st_gid)
        os.fchmod(descriptor, stat.S_IMODE(source.st_mode))
        temporary = os.fstat(descriptor)
    except OSError:
        raise DependencyError("MCP dependency update failed") from None
    if (temporary.st_uid, temporary.st_gid) != (
        source.st_uid,
        source.st_gid,
    ) or (
        stat.S_IMODE(temporary.st_mode) != stat.S_IMODE(source.st_mode)
    ):
        raise DependencyError("MCP dependency update failed")


def _atomic_write(path: Path, content: bytes, source: os.stat_result) -> None:
    descriptor = None
    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = None
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            _preserve_regular_file_metadata(temporary.fileno(), source)
        os.replace(temporary_name, path)
        temporary_name = None
    except DependencyError:
        raise
    except Exception:
        raise DependencyError("MCP dependency update failed") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


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
    try:
        spec = next(
            (item for item in discover_dependencies(hub) if item.server == server),
            None,
        )
    except OSError:
        raise DependencyError("MCP dependency config is unavailable") from None
    if spec is None:
        raise DependencyError("unknown MCP dependency server")
    if not _target_is_available(spec, version, fetch_json):
        raise DependencyError("MCP dependency target is unavailable")

    try:
        original = config_path.read_bytes()
    except OSError:
        raise DependencyError("MCP dependency config is unavailable") from None
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


def _load_merge_mcp():
    path = Path(__file__).with_name("merge-mcp.py")
    spec = importlib.util.spec_from_file_location("_agent_sync_merge_mcp", path)
    if spec is None or spec.loader is None:
        raise DependencyError("MCP smoke probe configuration is unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise DependencyError("MCP smoke probe configuration is unavailable") from None
    return module


def _smoke_launcher(
    hub: Path, server: str, target: str | None
) -> tuple[list[str], dict[str, str]]:
    try:
        document = json.loads(
            (hub / "mcp" / "shared-servers.json").read_text(encoding="utf-8")
        )
    except Exception:
        raise DependencyError("MCP smoke probe configuration is unavailable") from None
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    config = servers.get(server) if isinstance(servers, dict) else None
    if not isinstance(config, dict):
        raise DependencyError("unknown MCP smoke probe server")
    if config.get("type", "stdio") != "stdio" or "url" in config:
        raise DependencyError("MCP smoke probe requires a stdio launcher")

    command = config.get("command")
    args = config.get("args", [])
    environment = config.get("env", {})
    if (
        not isinstance(command, str)
        or not command
        or not isinstance(args, list)
        or any(not isinstance(value, str) for value in args)
        or not isinstance(environment, dict)
        or any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\0" in key
            or not isinstance(value, str)
            for key, value in environment.items()
        )
    ):
        raise DependencyError("invalid MCP smoke probe launcher")

    helpers = _load_merge_mcp()
    mapping: dict[str, str] = {}
    try:
        if target is not None and helpers.secret_donors_allowed(hub, target):
            donors = helpers.collect_donors()
            donor = donors.get(server.lower(), {}) if isinstance(donors, dict) else {}
            mapping = helpers.donor_lookup_map(donor)

        resolved_command = helpers.resolve_value(command, mapping)
        resolved_args = [helpers.resolve_value(value, mapping) for value in args]
        resolved_environment = {
            key: helpers.resolve_value(value, mapping)
            for key, value in environment.items()
        }
    except Exception:
        raise DependencyError("MCP smoke probe placeholder resolution failed") from None

    values = [resolved_command, *resolved_args, *resolved_environment.values()]
    if any(
        not isinstance(value, str)
        or "\0" in value
        or helpers.is_placeholder(value)
        for value in values
    ) or not resolved_command:
        raise DependencyError("unresolved MCP smoke probe placeholder")

    child_environment = dict(os.environ)
    child_environment.update(resolved_environment)
    return [resolved_command, *resolved_args], child_environment


class _ChildOutput:
    def __init__(self, process) -> None:
        self.process = process
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.limit_exceeded = threading.Event()
        self.threads: list[threading.Thread] = []

    def start_readers(self) -> None:
        readers = (
            (self._read_stdout, self.process.stdout, "mcp-smoke-reader-stdout"),
            (self._read_stderr, self.process.stderr, "mcp-smoke-reader-stderr"),
        )
        for target, stream, name in readers:
            thread = threading.Thread(
                target=target,
                args=(stream,),
                name=name,
                daemon=True,
            )
            self.threads.append(thread)
            thread.start()

    def _read_stdout(self, stream) -> None:
        buffered = bytearray()
        total = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    if buffered:
                        self.events.put(("stdout", bytes(buffered)))
                    self.events.put(("stdout", _STREAM_EOF))
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                total += len(chunk)
                if total > _MAX_CHILD_OUTPUT:
                    self.limit_exceeded.set()
                    self.events.put(("stdout", _STREAM_LIMIT))
                    return
                buffered.extend(chunk)
                while b"\n" in buffered:
                    line, _, remainder = buffered.partition(b"\n")
                    buffered = bytearray(remainder)
                    self.events.put(("stdout", line))
        except Exception:
            self.events.put(("stdout", _STREAM_EOF))

    def _read_stderr(self, stream) -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                total += len(chunk)
                if total > _MAX_CHILD_OUTPUT:
                    self.limit_exceeded.set()
                    self.events.put(("stderr", _STREAM_LIMIT))
                    return
        except Exception:
            return

    def response(self, request_id: int, deadline: float) -> dict[str, object]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DependencyError("MCP smoke probe timed out")
            try:
                stream, payload = self.events.get(timeout=remaining)
            except queue.Empty:
                raise DependencyError("MCP smoke probe timed out") from None
            if payload is _STREAM_LIMIT:
                raise DependencyError("MCP smoke probe output limit exceeded")
            if stream == "stderr":
                continue
            if payload is _STREAM_EOF:
                raise DependencyError("MCP smoke probe ended before responding")
            try:
                message = json.loads(
                    payload.decode("utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except Exception:
                raise DependencyError("invalid MCP smoke probe response") from None
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise DependencyError("invalid MCP smoke probe response")
            method = message.get("method")
            if "method" in message and "id" not in message:
                if (
                    isinstance(method, str)
                    and method
                    and "result" not in message
                    and "error" not in message
                    and (
                        "params" not in message
                        or isinstance(message["params"], dict)
                    )
                    and set(message) <= {"jsonrpc", "method", "params"}
                ):
                    continue
                raise DependencyError("invalid MCP smoke probe response")
            if "method" in message and "id" in message:
                incoming_id = message["id"]
                if (
                    not _valid_message_id(incoming_id)
                    or not isinstance(method, str)
                    or not method
                    or "result" in message
                    or "error" in message
                    or (
                        "params" in message
                        and not isinstance(message["params"], dict)
                    )
                    or not set(message).issubset(
                        {"jsonrpc", "id", "method", "params"}
                    )
                ):
                    raise DependencyError("invalid MCP smoke probe request")
                if method != "ping":
                    raise DependencyError("unsupported MCP smoke probe request")
                _send_mcp_message(
                    self.process,
                    {"jsonrpc": "2.0", "id": incoming_id, "result": {}},
                )
                continue
            message_id = message.get("id")
            if (
                not _valid_message_id(message_id)
                or type(message_id) is not type(request_id)
                or message_id != request_id
                or not set(message).issubset(
                    {"jsonrpc", "id", "result", "error"}
                )
                or ("result" in message) == ("error" in message)
            ):
                raise DependencyError("unexpected MCP smoke probe response")
            if "error" in message:
                raise DependencyError("MCP smoke probe returned an error")
            result = message.get("result")
            if not isinstance(result, dict):
                raise DependencyError("invalid MCP smoke probe response")
            return result

    def close_and_join(self) -> bool:
        closed = _close_process_streams(self.process)
        joined = True
        deadline = time.monotonic() + _SHUTDOWN_GRACE
        for thread in self.threads:
            try:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            except Exception:
                joined = False
        try:
            alive = any(thread.is_alive() for thread in self.threads)
        except Exception:
            alive = True
        return closed and joined and not alive


def _close_process_streams(process) -> bool:
    closed = True
    for stream_name in ("stdin", "stdout", "stderr"):
        try:
            getattr(process, stream_name).close()
        except Exception:
            closed = False
    return closed


def _valid_message_id(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _send_mcp_message(process, message: dict[str, object]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        process.stdin.write(payload)
        process.stdin.flush()
    except Exception:
        raise DependencyError("MCP smoke probe communication failed") from None


def _validate_initialization(initialize: dict[str, object]) -> str:
    protocol_version = initialize.get("protocolVersion")
    capabilities = initialize.get("capabilities")
    server_info = initialize.get("serverInfo")
    tools_capability = (
        capabilities.get("tools") if isinstance(capabilities, dict) else None
    )
    if protocol_version != _MCP_PROTOCOL_VERSION:
        raise DependencyError("unsupported MCP smoke probe protocol")
    if not isinstance(tools_capability, dict) or (
        "listChanged" in tools_capability
        and not isinstance(tools_capability["listChanged"], bool)
    ):
        raise DependencyError("invalid MCP smoke probe capabilities")
    if not isinstance(server_info, dict) or any(
        not isinstance(server_info.get(field), str)
        or not server_info[field].strip()
        or _CONTROL_CHARACTERS.search(server_info[field])
        for field in ("name", "version")
    ):
        raise DependencyError("invalid MCP smoke probe server info")
    return protocol_version


def _tool_page(listed: dict[str, object]) -> tuple[set[str], str | None]:
    tools = listed.get("tools")
    if not isinstance(tools, list) or any(
        not isinstance(tool, dict)
        or not isinstance(tool.get("name"), str)
        or not tool["name"]
        or _CONTROL_CHARACTERS.search(tool["name"])
        for tool in tools
    ):
        raise DependencyError("invalid MCP smoke probe tool list")
    if "nextCursor" not in listed:
        return {tool["name"] for tool in tools}, None
    cursor = listed["nextCursor"]
    if not isinstance(cursor, str):
        raise DependencyError("invalid MCP smoke probe cursor")
    return {tool["name"] for tool in tools}, cursor


def _signal_child(process, requested_signal: int, direct_method: str) -> bool:
    try:
        pid = process.pid
        if isinstance(pid, int) and pid > 0:
            os.killpg(pid, requested_signal)
            return True
    except Exception:
        pass
    try:
        getattr(process, direct_method)()
        return True
    except Exception:
        return False


def _wait_child(process) -> tuple[int | None, bool]:
    try:
        return process.wait(timeout=_SHUTDOWN_GRACE), False
    except subprocess.TimeoutExpired:
        return None, False
    except Exception:
        return None, True


def _close_child(process) -> tuple[int | None, bool]:
    cleanup_failed = False
    try:
        process.stdin.close()
    except Exception:
        cleanup_failed = True
    returncode, wait_failed = _wait_child(process)
    cleanup_failed = cleanup_failed or wait_failed
    if returncode is not None:
        if cleanup_failed:
            raise DependencyError("MCP smoke probe cleanup failed")
        return returncode, False

    if not _signal_child(process, signal.SIGTERM, "terminate"):
        cleanup_failed = True
    returncode, wait_failed = _wait_child(process)
    cleanup_failed = cleanup_failed or wait_failed
    if returncode is not None:
        if cleanup_failed:
            raise DependencyError("MCP smoke probe cleanup failed")
        return returncode, True

    if not _signal_child(process, signal.SIGKILL, "kill"):
        cleanup_failed = True
    returncode, wait_failed = _wait_child(process)
    cleanup_failed = cleanup_failed or wait_failed
    if returncode is None or cleanup_failed:
        raise DependencyError("MCP smoke probe cleanup failed")
    return returncode, True


def smoke_dependency(
    hub: Path,
    server: str,
    *,
    target: str | None = None,
    timeout: float = 30,
    runner=Popen,
) -> dict[str, object]:
    if (
        os.name != "posix"
        or not hasattr(os, "killpg")
        or not hasattr(signal, "SIGTERM")
        or not hasattr(signal, "SIGKILL")
    ):
        raise DependencyError("MCP smoke probe requires POSIX")
    if (
        not isinstance(server, str)
        or not _SAFE_SERVER_NAME.fullmatch(server)
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise DependencyError("invalid MCP smoke probe request")

    command, environment = _smoke_launcher(Path(hub), server, target)
    try:
        process = runner(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
            env=environment,
        )
    except Exception:
        raise DependencyError("MCP smoke probe launch failed") from None

    deadline = time.monotonic() + float(timeout)
    output = None
    failure: DependencyError | None = None
    result: dict[str, object] | None = None
    try:
        output = _ChildOutput(process)
        output.start_readers()
        _send_mcp_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "agent-sync",
                        "version": (
                            Path(__file__).parents[1] / "VERSION"
                        ).read_text(encoding="utf-8").strip(),
                    },
                },
            },
        )
        initialize = output.response(1, deadline)
        protocol_version = _validate_initialization(initialize)

        _send_mcp_message(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        names: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = None
        request_id = 2
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            _send_mcp_message(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                },
            )
            page_names, next_cursor = _tool_page(
                output.response(request_id, deadline)
            )
            names.update(page_names)
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise DependencyError("repeated MCP smoke probe cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            request_id += 1
        sorted_names = sorted(names)
        result = {
            "server": server,
            "initialized": True,
            "protocol_version": protocol_version,
            "tool_count": len(sorted_names),
            "tools": sorted_names,
            "status": "healthy",
        }
    except DependencyError as error:
        failure = error
    except Exception:
        failure = DependencyError("MCP smoke probe failed")

    cleanup_failure = None
    try:
        returncode, forced = _close_child(process)
    except DependencyError as error:
        returncode, forced = None, False
        cleanup_failure = error
    streams_clean = (
        output.close_and_join()
        if output is not None
        else _close_process_streams(process)
    )
    if not streams_clean and cleanup_failure is None:
        cleanup_failure = DependencyError("MCP smoke probe cleanup failed")
    if cleanup_failure is not None:
        raise cleanup_failure
    if failure is not None:
        raise failure
    if output is not None and output.limit_exceeded.is_set():
        raise DependencyError("MCP smoke probe output limit exceeded")
    if not forced and returncode not in (0, None):
        raise DependencyError("MCP smoke probe process failed")
    if result is None:
        raise DependencyError("MCP smoke probe failed")
    return result


def _default_hub() -> Path:
    configured = os.environ.get("AGENT_HUB_ROOT")
    return Path(configured) if configured else Path.home() / ".config" / "agent-hub"


def _dependency_requirement(spec: DependencySpec) -> str:
    if spec.pin_state == "exact" and spec.version is not None:
        separator = "==" if spec.ecosystem == "pypi" else "@"
        return f"{spec.package}{separator}{spec.version}"
    if spec.pin_state == "latest":
        return f"{spec.package}@latest"
    return spec.package


def _require_clean_hub(hub: Path) -> None:
    try:
        status = subprocess.run(
            ["git", "-C", str(hub), "status", "--porcelain"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        raise DependencyError("Hub must be a clean git worktree before --apply") from None
    if status.returncode != 0 or status.stdout:
        raise DependencyError("Hub must be a clean git worktree before --apply")


def _print_dependency_check(
    dependencies: list[dict[str, object]], *, as_json: bool, quiet: bool
) -> None:
    if quiet:
        for record in dependencies:
            if record["status"] == "update-available":
                print(
                    "MCP dependency update available: "
                    f"{record['server']}/{record['package']} "
                    f"{record['current']} -> {record['latest']}",
                    file=sys.stderr,
                )
        return
    if as_json:
        print(json.dumps({"dependencies": dependencies}, sort_keys=True))
        return
    for record in dependencies:
        current = record["current"] or record["pin_state"]
        latest = record["latest"] or "unknown"
        print(
            f"{record['server']}: {record['package']} "
            f"{current} -> {latest} ({record['status']})"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-deps")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="check primary registries for updates")
    check.add_argument("--json", action="store_true", dest="as_json")
    check.add_argument("--refresh", action="store_true")
    check.add_argument("--cached", action="store_true", help=argparse.SUPPRESS)
    check.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    check.add_argument("--hub", type=Path, default=None, help=argparse.SUPPRESS)

    pin = commands.add_parser("pin", help="propose or apply an exact dependency pin")
    pin.add_argument("server")
    pin.add_argument("version")
    mode = pin.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    pin.add_argument("--hub", type=Path, default=None, help=argparse.SUPPRESS)

    smoke = commands.add_parser("smoke", help="probe one configured MCP server")
    smoke.add_argument("server")
    smoke.add_argument("--target")
    smoke.add_argument("--timeout", type=float, default=30)
    smoke.add_argument("--hub", type=Path, default=None, help=argparse.SUPPRESS)
    return parser


def main(
    argv: list[str] | None = None, *, fetch_json=default_fetch_json
) -> int:
    args = _build_parser().parse_args(argv)
    hub = args.hub if args.hub is not None else _default_hub()
    try:
        if args.command == "check":
            if args.cached and args.refresh:
                raise DependencyError("--cached and --refresh cannot be combined")
            cached = None if args.refresh else _read_cache()
            if cached is None and args.cached:
                return 0
            if cached is None:
                dependencies = check_dependencies(
                    discover_dependencies(hub), fetch_json
                )
                checked_at = time.time()
                _write_cache(dependencies, checked_at)
            else:
                dependencies = cached["dependencies"]
            _print_dependency_check(
                dependencies, as_json=args.as_json, quiet=args.quiet
            )
            return 0

        if args.command == "pin":
            if not _NUMERIC_VERSION.fullmatch(args.version):
                raise DependencyError("invalid MCP dependency target version")
            specs = discover_dependencies(hub)
            spec = next((item for item in specs if item.server == args.server), None)
            if spec is None:
                raise DependencyError("unknown MCP dependency server")
            if args.apply:
                _require_clean_hub(hub)
            old_requirement = _dependency_requirement(spec)
            separator = "==" if spec.ecosystem == "pypi" else "@"
            new_requirement = f"{spec.package}{separator}{args.version}"
            pin_dependency(
                hub,
                args.server,
                args.version,
                apply=args.apply,
                fetch_json=fetch_json,
            )
            print(f"{args.server}: {old_requirement} -> {new_requirement}")
            return 0

        result = smoke_dependency(
            hub,
            args.server,
            target=args.target,
            timeout=args.timeout,
        )
        print(f"{result['server']}: healthy ({result['tool_count']} tools)")
        return 0
    except DependencyError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("ERROR: MCP dependency command failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
