"""Single-file UEFN Thumbnail Creator.

The implementation is organized into module-like sections so the deployed tool
remains easy to navigate while requiring only this one Python file.
"""

from __future__ import annotations

import colorsys
import copy
import importlib
import json
import math
import os
import re
import shutil
import site
import subprocess
import sys
import time
import types
import uuid
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from io import BytesIO
from typing import Any, Iterable

import unreal


__version__ = "1.0.0"

# Release metadata is intentionally per-tool: updating another script in this
# repository must never make Thumbnail Creator replace itself.  Release tags are
# immutable and contain the exact script that the updater consumes.
_REPO_URL = "https://github.com/supremeuefn/uefn-python-tools"
_RAW_BASE = "https://raw.githubusercontent.com/supremeuefn/uefn-python-tools"
_UPDATE_DIR = "tools/thumbnail_creator"
_VERSION_URL = _RAW_BASE + "/main/" + _UPDATE_DIR + "/VERSION.txt"
_CHANGELOG_URL = _RAW_BASE + "/main/" + _UPDATE_DIR + "/CHANGELOG.md"
_TAG_PREFIX = "thumbnail-creator/v"
_UPDATE_RELOAD_ENV = "THUMBNAIL_CREATOR_UPDATED"
_UPDATE_SETTINGS_FILE = "update_settings.json"
_DEFAULT_UPDATE_SETTINGS = {
    "check_updates_on_launch": True,
    "auto_install_updates": False,
}


def _version_tuple(value: str) -> tuple[int, int, int]:
    """Parse the strict three-component release format used by this tool."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if match is None:
        raise ValueError("Invalid Thumbnail Creator version: %r" % value)
    return tuple(int(part) for part in match.groups())


def _http_get(url: str, timeout: float) -> bytes | None:
    """Fetch a small release file without making networking a launch gate."""
    import urllib.request

    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        url + separator + "cb=" + __version__,
        headers={
            "User-Agent": "ThumbnailCreator/" + __version__,
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def _tool_url_for_version(version: str) -> str:
    tag = _TAG_PREFIX + str(version)
    return _RAW_BASE + "/" + tag + "/tools/thumbnail_creator_tool.py"


def check_for_update(*, include_changelog: bool = False) -> dict[str, str] | None:
    """Return metadata for a newer release, or ``None`` when current/offline."""
    if os.environ.get(_UPDATE_RELOAD_ENV):
        return None
    raw = _http_get(_VERSION_URL, timeout=3.0)
    if not raw:
        return None
    remote = raw.decode("utf-8", "replace").strip().splitlines()[0].strip()
    try:
        newer = _version_tuple(remote) > _version_tuple(__version__)
    except ValueError:
        return None
    if not newer:
        return None
    changelog = ""
    if include_changelog:
        notes = _http_get(_CHANGELOG_URL, timeout=5.0)
        if notes:
            changelog = notes.decode("utf-8", "replace").strip()
    return {
        "version": remote,
        "changelog": changelog,
        "tool_url": _tool_url_for_version(remote),
    }


def incoming_release_notes(changelog: str, current: str = __version__) -> str:
    """Return only changelog sections newer than ``current`` as plain text."""
    if not changelog:
        return "Release notes are unavailable."
    parts = re.split(r"(?m)^##\s+v([\d.]+).*$", changelog)
    releases: list[tuple[tuple[int, int, int], str, str]] = []
    current_tuple = _version_tuple(current)
    for index in range(1, len(parts), 2):
        version = parts[index].strip()
        body = parts[index + 1].strip() if index + 1 < len(parts) else ""
        try:
            parsed = _version_tuple(version)
        except ValueError:
            continue
        if parsed > current_tuple:
            releases.append((parsed, version, body))
    releases.sort(reverse=True)
    if not releases:
        return "Release notes are unavailable."
    return "\n\n".join("v%s\n%s" % (version, body) for _, version, body in releases)


def _embedded_version(source: str) -> str | None:
    match = re.search(
        r"(?m)^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$", source
    )
    return match.group(1) if match else None


def download_update_payload(version: str) -> dict[str, object]:
    """Download and validate an immutable tagged release in memory."""
    _version_tuple(version)
    payload = _http_get(_tool_url_for_version(version), timeout=30.0)
    if not payload:
        raise RuntimeError("The tagged release could not be downloaded.")
    source = payload.decode("utf-8")
    if _embedded_version(source) != version:
        raise RuntimeError("The downloaded script version does not match the release.")
    path = _self_path()
    code = compile(source, path, "exec")
    return {
        "version": version,
        "payload": payload,
        "code": code,
    }


def _self_path() -> str:
    """Locate this single-file tool even when UEFN omitted ``__file__``."""
    candidate = globals().get("__file__")
    if candidate and os.path.isfile(candidate):
        return os.path.abspath(candidate)
    code_path = _self_path.__code__.co_filename
    if code_path and os.path.isfile(code_path):
        return os.path.abspath(code_path)
    raise RuntimeError("Thumbnail Creator could not locate its script on disk.")


# UEFN may remove ``__file__`` from the execution namespace as soon as an
# Execute Python Script command returns.  Keep a stable copy for callbacks,
# retries, and background work that outlive that initial execution.
SCRIPT_PATH = _self_path()


def apply_validated_update(update: dict[str, object]) -> bool:
    """Atomically install, reload, and roll back a validated release on failure."""
    path = _self_path()
    payload = update.get("payload")
    code = update.get("code")
    if not isinstance(payload, bytes) or not isinstance(code, types.CodeType):
        raise ValueError("The update payload was not validated.")

    staged = path + ".new"
    backup = path + ".bak"
    with open(staged, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copy2(path, backup)
    os.replace(staged, path)

    old_environment = os.environ.get(_UPDATE_RELOAD_ENV)
    os.environ[_UPDATE_RELOAD_ENV] = "1"
    try:
        close_window()
        exec(code, {"__name__": "__main__", "__file__": path})
    except Exception as update_error:
        try:
            close_window()
        except Exception:
            pass
        os.replace(backup, path)
        try:
            with open(path, "rb") as handle:
                previous_payload = handle.read()
            previous_code = compile(previous_payload.decode("utf-8"), path, "exec")
            exec(previous_code, {"__name__": "__main__", "__file__": path})
        except Exception as rollback_error:
            raise RuntimeError(
                "Update failed (%s) and rollback could not relaunch (%s)."
                % (update_error, rollback_error)
            ) from update_error
        raise RuntimeError(
            "The update failed at startup and the previous version was restored: %s"
            % update_error
        ) from update_error
    else:
        try:
            os.remove(backup)
        except OSError:
            pass
        return True
    finally:
        if old_environment is None:
            os.environ.pop(_UPDATE_RELOAD_ENV, None)
        else:
            os.environ[_UPDATE_RELOAD_ENV] = old_environment


Image = None
ImageChops = None
ImageEnhance = None
ImageFilter = None


def _load_pillow_modules() -> None:
    """Load Pillow after the tool-owned dependency directory is activated."""
    global Image, ImageChops, ImageEnhance, ImageFilter
    if Image is not None:
        return
    activate_vendor_path()
    from PIL import Image as pillow_image
    from PIL import ImageChops as pillow_chops
    from PIL import ImageEnhance as pillow_enhance
    from PIL import ImageFilter as pillow_filter

    Image = pillow_image
    ImageChops = pillow_chops
    ImageEnhance = pillow_enhance
    ImageFilter = pillow_filter




# ============================================================================
# Models
# ============================================================================

class SourceKind(str, Enum):
    STATIC_MESH = "static_mesh"
    SKELETAL_MESH = "skeletal_mesh"
    BLUEPRINT = "blueprint"
    ACTORS = "actors"
    NIAGARA = "niagara"
    WHOLE_VIEW = "whole_view"


class LightingMode(str, Enum):
    WORLD = "world"
    STUDIO = "studio"


LIGHTING_PRESET_IDS = ("neutral", "soft", "dramatic", "flat")
STUDIO_LIGHT_ROLES = ("key", "fill", "rim")
CUSTOM_LIGHTING_PRESET_PREFIX = "custom:"


@dataclass
class CaptureSource:
    kind: SourceKind
    paths: list[str] = field(default_factory=list)
    display_name: str = ""
    niagara_time: float = 0.0

    @property
    def key(self) -> str:
        return "%s:%s" % (self.kind.value, "|".join(self.paths))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptureSource":
        return cls(
            kind=SourceKind(data.get("kind", SourceKind.STATIC_MESH.value)),
            paths=list(data.get("paths") or []),
            display_name=str(data.get("display_name") or ""),
            niagara_time=float(data.get("niagara_time", 0.0)),
        )


@dataclass
class CameraState:
    yaw: float = 35.0
    pitch: float = 20.0
    roll: float = 0.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    dolly: float = 1.0
    fov: float = 35.0
    framing_margin: float = 1.18
    auto_fit: bool = True


@dataclass
class AdjustState:
    hue: float = 0.0
    saturation: float = 1.0
    brightness: float = 1.0
    contrast: float = 1.0
    exposure: float = 0.0
    outline_width: int = 0
    outline_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    outer_silhouette_only: bool = False


def _clamped_float(value, default, minimum, maximum) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(float(minimum), min(float(maximum), number))


@dataclass
class StudioLightState:
    intensity_multiplier: float = 1.0
    temperature_offset: int = 0
    size_multiplier: float = 1.0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cast_shadows: bool = True

    def __post_init__(self):
        self.intensity_multiplier = _clamped_float(
            self.intensity_multiplier, 1.0, 0.0, 3.0
        )
        try:
            offset = int(float(self.temperature_offset))
        except (TypeError, ValueError):
            offset = 0
        self.temperature_offset = max(-4000, min(4000, offset))
        self.size_multiplier = _clamped_float(
            self.size_multiplier, 1.0, 0.1, 5.0
        )
        values = self.position if isinstance(self.position, (list, tuple)) else ()
        if len(values) != 3:
            values = (0.0, 0.0, 0.0)
        self.position = tuple(
            _clamped_float(value, 0.0, -5.0, 5.0) for value in values
        )
        self.cast_shadows = bool(self.cast_shadows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intensity_multiplier": self.intensity_multiplier,
            "temperature_offset": self.temperature_offset,
            "size_multiplier": self.size_multiplier,
            "position": list(self.position),
            "cast_shadows": self.cast_shadows,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        default: "StudioLightState | None" = None,
    ) -> "StudioLightState":
        source = dict(data) if isinstance(data, dict) else {}
        fallback = default or cls()
        return cls(
            intensity_multiplier=source.get(
                "intensity_multiplier", fallback.intensity_multiplier
            ),
            temperature_offset=source.get(
                "temperature_offset", fallback.temperature_offset
            ),
            size_multiplier=source.get(
                "size_multiplier", fallback.size_multiplier
            ),
            position=source.get("position", fallback.position),
            cast_shadows=source.get("cast_shadows", fallback.cast_shadows),
        )


def _neutral_key() -> StudioLightState:
    return StudioLightState(1.0, -400, 1.8, (2.8, -1.8, 2.2), True)


def _neutral_fill() -> StudioLightState:
    return StudioLightState(0.35, 200, 2.4, (2.4, 2.0, 0.8), False)


def _neutral_rim() -> StudioLightState:
    return StudioLightState(0.55, 1200, 1.2, (-2.2, 0.3, 2.0), True)


@dataclass
class StudioRigState:
    key: StudioLightState = field(default_factory=_neutral_key)
    fill: StudioLightState = field(default_factory=_neutral_fill)
    rim: StudioLightState = field(default_factory=_neutral_rim)

    def __post_init__(self):
        defaults = (_neutral_key(), _neutral_fill(), _neutral_rim())
        for role, default in zip(STUDIO_LIGHT_ROLES, defaults):
            value = getattr(self, role)
            if not isinstance(value, StudioLightState):
                value = StudioLightState.from_dict(value, default)
            setattr(self, role, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            role: getattr(self, role).to_dict()
            for role in STUDIO_LIGHT_ROLES
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StudioRigState":
        source = dict(data) if isinstance(data, dict) else {}
        defaults = cls()
        return cls(
            **{
                role: StudioLightState.from_dict(
                    source.get(role), getattr(defaults, role)
                )
                for role in STUDIO_LIGHT_ROLES
            }
        )


@dataclass
class LightingState:
    mode: LightingMode = LightingMode.WORLD
    preset: str = "neutral"
    preset_name: str = ""
    intensity: float = 1.0
    temperature_kelvin: int = 6500
    rig: StudioRigState | None = None

    def __post_init__(self):
        if not isinstance(self.mode, LightingMode):
            try:
                self.mode = LightingMode(str(self.mode).lower())
            except ValueError:
                self.mode = LightingMode.WORLD
        self.preset = str(self.preset or "neutral").strip().lower()
        custom = self.preset.startswith(CUSTOM_LIGHTING_PRESET_PREFIX)
        if self.preset not in LIGHTING_PRESET_IDS and not custom:
            self.preset = "neutral"
        self.preset_name = str(self.preset_name or "").strip()[:64]
        try:
            intensity = float(self.intensity)
        except (TypeError, ValueError):
            intensity = 1.0
        try:
            temperature_kelvin = int(float(self.temperature_kelvin))
        except (TypeError, ValueError):
            temperature_kelvin = 6500
        self.intensity = max(0.1, min(2.0, intensity))
        self.temperature_kelvin = max(
            2500, min(10000, temperature_kelvin)
        )
        if self.rig is not None and not isinstance(self.rig, StudioRigState):
            self.rig = StudioRigState.from_dict(self.rig)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "mode": self.mode.value,
            "preset": self.preset,
            "intensity": self.intensity,
            "temperature_kelvin": self.temperature_kelvin,
        }
        if self.preset_name:
            data["preset_name"] = self.preset_name
        if self.rig is not None:
            data["rig"] = self.rig.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        default_mode: LightingMode = LightingMode.WORLD,
    ) -> "LightingState":
        data = dict(data) if isinstance(data, dict) else {}
        return cls(
            mode=data.get("mode", default_mode.value),
            preset=data.get("preset", "neutral"),
            preset_name=data.get("preset_name", ""),
            intensity=data.get("intensity", 1.0),
            temperature_kelvin=data.get("temperature_kelvin", 6500),
            rig=data.get("rig"),
        )


@dataclass
class ExportOptions:
    output_size: int = 512
    supersample: int = 2
    transparent: bool = True
    background_color: tuple[int, int, int, int] = (32, 32, 32, 255)
    output_directory: str = ""
    import_texture: bool = True
    import_path: str = ""
    naming_pattern: str = "{name}_icon_{size}"
    preset_name: str = "Default"


@dataclass
class CaptureRequest:
    source: CaptureSource
    camera: CameraState = field(default_factory=CameraState)
    adjust: AdjustState = field(default_factory=AdjustState)
    lighting: LightingState = field(default_factory=LightingState)
    export: ExportOptions = field(default_factory=ExportOptions)
    preview: bool = False
    preview_fast: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "camera": asdict(self.camera),
            "adjust": asdict(self.adjust),
            "lighting": self.lighting.to_dict(),
            "export": asdict(self.export),
            "preview": self.preview,
            "preview_fast": self.preview_fast,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptureRequest":
        camera = dict(data.get("camera") or {})
        adjust = dict(data.get("adjust") or {})
        export = dict(data.get("export") or {})
        lighting_data = data.get("lighting")
        if "outline_color" in adjust:
            adjust["outline_color"] = tuple(adjust["outline_color"])
        if "background_color" in export:
            export["background_color"] = tuple(export["background_color"])
        return cls(
            source=CaptureSource.from_dict(data.get("source") or {}),
            camera=CameraState(**camera),
            adjust=AdjustState(**adjust),
            lighting=LightingState.from_dict(lighting_data),
            export=ExportOptions(**export),
            preview=bool(data.get("preview", False)),
            preview_fast=bool(data.get("preview_fast", True)),
        )


@dataclass
class CaptureResult:
    success: bool
    source_key: str
    png_path: str = ""
    texture_path: str = ""
    error: str = ""
    output_size: int = 0
    capture_size: int = 0
    elapsed_seconds: float = 0.0
    alpha_nonzero: int = 0
    alpha_fractional: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# Storage
# ============================================================================

SCHEMA_VERSION = 2


class LightingPresetError(ValueError):
    pass


def default_saved_directory() -> str:
    script_dir = os.path.dirname(SCRIPT_PATH)
    content_dir = os.path.dirname(os.path.dirname(script_dir))
    project_dir = os.path.dirname(content_dir)
    if os.path.basename(content_dir).lower() == "content":
        return os.path.join(project_dir, "Saved", "ThumbnailCreator")
    try:
        import unreal

        return os.path.abspath(
            os.path.join(unreal.Paths.project_saved_dir(), "ThumbnailCreator")
        )
    except Exception:
        return os.path.abspath(os.path.join(os.getcwd(), "Saved", "ThumbnailCreator"))


def update_settings_path() -> str:
    return os.path.join(default_saved_directory(), _UPDATE_SETTINGS_FILE)


def load_update_settings() -> dict[str, bool]:
    settings = dict(_DEFAULT_UPDATE_SETTINGS)
    try:
        with open(update_settings_path(), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            for key in _DEFAULT_UPDATE_SETTINGS:
                if key in stored:
                    settings[key] = bool(stored[key])
    except Exception:
        pass
    return settings


def save_update_settings(settings: dict[str, bool]) -> bool:
    try:
        path = update_settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            key: bool(settings.get(key, default))
            for key, default in _DEFAULT_UPDATE_SETTINGS.items()
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return True
    except Exception:
        return False


class JsonStore:
    def __init__(
        self,
        root: str | None = None,
        *,
        schema_version: int = SCHEMA_VERSION,
    ):
        self.root = os.path.abspath(root or default_saved_directory())
        self.schema_version = int(schema_version)
        os.makedirs(self.root, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def read(self, name: str, default: Any) -> Any:
        path = self.path(name)
        if not os.path.isfile(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            if not isinstance(document, dict):
                raise ValueError("The JSON root must be an object")
            if int(document.get("schema_version", 0)) > self.schema_version:
                raise ValueError("Unsupported future schema version")
            return document.get("data", default)
        except Exception:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = "%s.corrupt_%s" % (path, stamp)
            try:
                shutil.copy2(path, backup)
            except Exception:
                pass
            return default

    def write(self, name: str, data: Any) -> str:
        path = self.path(name)
        temp = path + ".tmp"
        document = {
            "schema_version": self.schema_version,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "data": data,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temp, path)
        return path


class ThumbnailCreatorStore:
    def __init__(self, root: str | None = None):
        self.json = JsonStore(root)

    @property
    def root(self) -> str:
        return self.json.root

    def load_lighting_presets(self) -> dict[str, dict[str, Any]]:
        data = self.json.read("lighting_presets.json", {})
        if not isinstance(data, dict):
            return {}
        valid = {}
        for identifier, raw in data.items():
            if not isinstance(raw, dict):
                continue
            preset_id = str(raw.get("id") or identifier).strip()
            name = str(raw.get("name") or "").strip()
            lighting = raw.get("lighting")
            if (
                not preset_id
                or not name
                or len(name) > 64
                or not isinstance(lighting, dict)
                or not isinstance(lighting.get("rig"), dict)
            ):
                continue
            record = copy.deepcopy(raw)
            record["id"] = preset_id
            record["name"] = name
            valid[preset_id] = record
        return valid

    def save_lighting_presets(self, data: dict[str, Any]) -> str:
        return self.json.write("lighting_presets.json", data)

    @staticmethod
    def _lighting_preset_name(
        name: str,
        records: dict[str, dict[str, Any]],
        exclude_id: str = "",
    ) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise LightingPresetError("Preset name is required.")
        if len(normalized) > 64:
            raise LightingPresetError("Preset name cannot exceed 64 characters.")
        folded = normalized.casefold()
        for identifier, record in records.items():
            if identifier != exclude_id and str(record.get("name", "")).casefold() == folded:
                raise LightingPresetError("A lighting preset with this name already exists.")
        return normalized

    @staticmethod
    def _lighting_payload(
        identifier: str,
        name: str,
        lighting: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(lighting, dict) or not isinstance(lighting.get("rig"), dict):
            raise LightingPresetError("A custom lighting preset requires a complete rig snapshot.")
        payload = copy.deepcopy(lighting)
        payload["mode"] = "studio"
        payload["preset"] = CUSTOM_LIGHTING_PRESET_PREFIX + identifier
        payload["preset_name"] = name
        return payload

    def create_lighting_preset(
        self,
        name: str,
        lighting: dict[str, Any],
    ) -> dict[str, Any]:
        records = self.load_lighting_presets()
        name = self._lighting_preset_name(name, records)
        identifier = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        record = {
            "id": identifier,
            "name": name,
            "lighting": self._lighting_payload(identifier, name, lighting),
            "created_at": now,
            "updated_at": now,
        }
        records[identifier] = record
        self.save_lighting_presets(records)
        return copy.deepcopy(record)

    def update_lighting_preset(
        self,
        identifier: str,
        *,
        name: str | None = None,
        lighting: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = str(identifier or "").strip()
        records = self.load_lighting_presets()
        if identifier not in records:
            raise LightingPresetError("The lighting preset no longer exists.")
        record = records[identifier]
        next_name = self._lighting_preset_name(
            record["name"] if name is None else name,
            records,
            exclude_id=identifier,
        )
        source_lighting = record["lighting"] if lighting is None else lighting
        record["name"] = next_name
        record["lighting"] = self._lighting_payload(
            identifier, next_name, source_lighting
        )
        record["updated_at"] = datetime.now().isoformat(timespec="seconds")
        records[identifier] = record
        self.save_lighting_presets(records)
        return copy.deepcopy(record)

    def rename_lighting_preset(
        self,
        identifier: str,
        name: str,
    ) -> dict[str, Any]:
        return self.update_lighting_preset(identifier, name=name)

    def delete_lighting_preset(self, identifier: str) -> bool:
        identifier = str(identifier or "").strip()
        records = self.load_lighting_presets()
        if identifier not in records:
            return False
        del records[identifier]
        self.save_lighting_presets(records)
        return True

    def load_session(self) -> dict[str, Any]:
        data = self.json.read("last_session.json", {})
        return data if isinstance(data, dict) else {}

    def save_session(self, data: dict[str, Any]) -> str:
        return self.json.write("last_session.json", data)


# ============================================================================
# Project Paths
# ============================================================================

class ContentPathError(ValueError):
    pass


_INTERNAL_ROOTS = {"engine", "fortnitegame", "memory", "script", "temp", "transient"}


def normalize_content_path(value: str) -> str:
    """Normalize an Unreal package folder and reject object/traversal paths."""
    value = str(value or "").strip().replace("\\", "/")
    if not value.startswith("/"):
        raise ContentPathError("Content paths must start with '/'.")
    parts = [part for part in value.split("/") if part]
    if not parts:
        raise ContentPathError("A content root is required.")
    if any(part in {".", ".."} for part in parts):
        raise ContentPathError("Content paths cannot contain '.' or '..'.")
    if any("." in part or ":" in part or "'" in part or '"' in part for part in parts):
        raise ContentPathError("Use a package folder, not an asset/object path.")
    return "/" + "/".join(parts)


def content_root_from_reference(value: str) -> str | None:
    """Extract the first non-engine mount point from an Unreal object reference."""
    candidates = re.findall(r"/([^/'\".:\s]+)(?=/|$)", str(value or ""))
    for candidate in candidates:
        if candidate.lower() not in _INTERNAL_ROOTS:
            return "/" + candidate
    return None


def detect_project_content_root(unreal_module=None) -> str:
    """Detect the active project mount point, falling back to standard UE ``/Game``."""
    if unreal_module is None:
        try:
            import unreal as unreal_module
        except ImportError:
            return "/Game"

    try:
        world = unreal_module.EditorLevelLibrary.get_editor_world()
        outer = world.get_outermost() if world is not None else None
        for obj in (outer, world):
            if obj is None:
                continue
            for getter_name in ("get_path_name", "get_name"):
                getter = getattr(obj, getter_name, None)
                if getter:
                    root = content_root_from_reference(getter())
                    if root:
                        return root
    except Exception:
        pass

    try:
        for asset in unreal_module.EditorUtilityLibrary.get_selected_assets():
            root = content_root_from_reference(asset.get_path_name())
            if root:
                return root
    except Exception:
        pass

    try:
        project_file = str(unreal_module.Paths.get_project_file_path())
        project_name = os.path.splitext(os.path.basename(project_file))[0]
        if project_name and project_name.lower() not in _INTERNAL_ROOTS:
            return "/" + project_name
    except Exception:
        pass
    return "/Game"


def default_import_path(unreal_module=None) -> str:
    return detect_project_content_root(unreal_module) + "/Textures/GeneratedThumbnails"


def project_destination_or_default(
    destination: str,
    content_root: str,
    fallback: str | None = None,
) -> str:
    """Keep valid project destinations and retarget stale cross-project paths."""
    content_root = normalize_content_path(content_root)
    fallback = validate_destination_path(
        fallback or (content_root + "/Textures/GeneratedThumbnails"),
        content_root,
    )
    try:
        return validate_destination_path(destination, content_root)
    except ContentPathError:
        return fallback


def validate_destination_path(destination: str, content_root: str) -> str:
    destination = normalize_content_path(destination)
    content_root = normalize_content_path(content_root)
    if destination != content_root and not destination.startswith(content_root + "/"):
        raise ContentPathError(
            "Texture destination must be under the active project root %s/." % content_root
        )
    return destination


# ============================================================================
# Pillow Support
# ============================================================================

RUNTIME_DEPENDENCIES = {
    "Pillow": ("PIL.Image", "Pillow>=10.4,<13"),
}


def package_directory() -> str:
    return os.path.join(default_saved_directory(), "python_packages")


def activate_vendor_path(path: str | None = None) -> str:
    if path is None:
        path = package_directory()
    path = os.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    site.addsitedir(path)
    # The tool-owned environment wins over unrelated packages installed into the
    # editor globally.  ``addsitedir`` appends, so explicitly move it to the front.
    normalized = os.path.normcase(os.path.abspath(path))
    sys.path[:] = [
        entry
        for entry in sys.path
        if os.path.normcase(os.path.abspath(entry or os.curdir)) != normalized
    ]
    sys.path.insert(0, path)
    return path


def _clear_runtime_dependency_cache() -> None:
    for module_name in list(sys.modules):
        if module_name == "PIL" or module_name.startswith("PIL."):
            del sys.modules[module_name]


def _pillow_version_supported() -> bool:
    try:
        pillow = importlib.import_module("PIL")
        version = tuple(
            int(match.group(0))
            for part in str(getattr(pillow, "__version__", "0")).split(".")[:2]
            if (match := re.match(r"\d+", part)) is not None
        )
        return version >= (10, 4) and version < (13, 0)
    except (ImportError, AttributeError, ValueError):
        return False


def missing_runtime_dependencies(
    names: list[str] | None = None,
    *,
    package_path: str | None = None,
) -> list[str]:
    """Return unavailable dependencies from ``names`` (all when omitted)."""
    activate_vendor_path(package_path)
    selected = list(names) if names is not None else list(RUNTIME_DEPENDENCIES)
    unknown = [name for name in selected if name not in RUNTIME_DEPENDENCIES]
    if unknown:
        raise ValueError("Unknown runtime dependencies: %s" % ", ".join(unknown))
    missing = []
    for display_name in selected:
        module_name, _requirement = RUNTIME_DEPENDENCIES[display_name]
        try:
            importlib.import_module(module_name)
        except (ImportError, AttributeError):
            missing.append(display_name)
            continue
        if display_name == "Pillow" and not _pillow_version_supported():
            missing.append(display_name)
    return missing


def _embedded_python() -> str:
    candidate = "<unresolved>"
    try:
        import unreal

        engine = unreal.Paths.convert_relative_path_to_full(unreal.Paths.engine_dir())
        candidate = os.path.join(
            engine, "Binaries", "ThirdParty", "Python3", "Win64", "python.exe"
        )
        if os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    if os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    raise RuntimeError(
        "UEFN's bundled python.exe could not be located. "
        "Expected: %s (sys.executable: %s)" % (candidate, sys.executable)
    )


def _run_pip(command: list[str], on_line=None) -> int:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if on_line is None:
        return subprocess.call(command, creationflags=creationflags)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if process.stdout is not None:
        for line in process.stdout:
            on_line(line.rstrip())
        process.stdout.close()
    return process.wait()


def install_runtime_dependencies(
    names: list[str] | None = None,
    *,
    python_exe: str | None = None,
    target: str | None = None,
    on_line=None,
) -> str:
    """Install missing UI/image dependencies into the tool-owned Saved folder."""
    target = activate_vendor_path(target)
    if python_exe is None:
        python_exe = _embedded_python()
    selected = list(names) if names is not None else list(RUNTIME_DEPENDENCIES)
    unknown = [name for name in selected if name not in RUNTIME_DEPENDENCIES]
    if unknown:
        raise ValueError("Unknown runtime dependencies: %s" % ", ".join(unknown))
    requirements = [RUNTIME_DEPENDENCIES[name][1] for name in selected]
    try:
        _run_pip([python_exe, "-m", "ensurepip"], on_line=on_line)
    except Exception as exc:
        if on_line is not None:
            on_line("ensurepip: %s" % exc)
    command = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        "--target",
        target,
    ] + requirements
    result = _run_pip(command, on_line=on_line)
    if result != 0:
        raise RuntimeError("pip exited with code %d." % result)
    importlib.invalidate_caches()
    _clear_runtime_dependency_cache()
    activate_vendor_path(target)
    remaining = missing_runtime_dependencies(selected, package_path=target)
    if remaining:
        raise RuntimeError(
            "Dependency installation completed but imports still fail: %s"
            % ", ".join(remaining)
        )
    return target


# ============================================================================
# Naming
# ============================================================================

def safe_name(value: str, fallback: str = "Icon") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "").strip("._")
    return value or fallback


def source_name(path: str) -> str:
    leaf = path.rsplit("/", 1)[-1].split(".", 1)[0]
    return safe_name(leaf)


def render_pattern(
    pattern: str,
    *,
    source_path: str,
    index: int,
    preset: str,
    size: int,
) -> str:
    normalized = source_path.replace("\\", "/").rstrip("/")
    name = source_name(normalized)
    parent = safe_name(normalized.rsplit("/", 2)[-2] if "/" in normalized else "")
    values = {
        "name": name,
        "parent": parent,
        "index": "%03d" % int(index),
        "date": datetime.now().strftime("%Y%m%d"),
        "preset": safe_name(preset, "Default"),
        "size": str(int(size)),
    }
    try:
        rendered = pattern.format(**values)
    except KeyError as exc:
        raise ValueError("Unknown naming token: %s" % exc) from exc
    return safe_name(rendered, name + "_icon")


def unique_png_path(directory: str, stem: str, reserved: set[str] | None = None) -> str:
    reserved = reserved if reserved is not None else set()
    base = os.path.abspath(os.path.join(directory, safe_name(stem) + ".png"))
    candidate = base
    suffix = 2
    while candidate.lower() in reserved:
        candidate = os.path.splitext(base)[0] + "_%d.png" % suffix
        suffix += 1
    reserved.add(candidate.lower())
    return candidate


# ============================================================================
# Visual Bounds
# ============================================================================

Vector3 = tuple[float, float, float]
AxisIntervals = dict[int, tuple[float, float]]


@dataclass(frozen=True)
class Bounds3D:
    minimum: Vector3
    maximum: Vector3

    @property
    def center(self) -> Vector3:
        return tuple(
            (minimum + maximum) * 0.5
            for minimum, maximum in zip(self.minimum, self.maximum)
        )

    @property
    def extent(self) -> Vector3:
        return tuple(
            max(0.0, (maximum - minimum) * 0.5)
            for minimum, maximum in zip(self.minimum, self.maximum)
        )

    @property
    def radius(self) -> float:
        return math.sqrt(sum(value * value for value in self.extent))

    @property
    def maximum_span(self) -> float:
        return max(
            maximum - minimum
            for minimum, maximum in zip(self.minimum, self.maximum)
        )


def bounds_from_center_extent(center: Vector3, extent: Vector3) -> Bounds3D:
    return Bounds3D(
        tuple(value - radius for value, radius in zip(center, extent)),
        tuple(value + radius for value, radius in zip(center, extent)),
    )


def pixel_bounds_touch_edge(
    pixel_bounds: tuple[int, int, int, int] | None,
    size: int,
    padding: int = 2,
) -> bool:
    if not pixel_bounds:
        return False
    min_x, min_y, max_x, max_y = pixel_bounds
    edge = max(0, int(padding))
    return (
        min_x <= edge
        or min_y <= edge
        or max_x >= int(size) - 1 - edge
        or max_y >= int(size) - 1 - edge
    )


def projection_intervals(
    pixel_bounds: tuple[int, int, int, int],
    size: int,
    ortho_width: float,
    view_center: Vector3,
    right: Vector3,
    up: Vector3,
    forward: Vector3,
) -> AxisIntervals:
    """Map a pixel rectangle to conservative intervals on its two world axes."""
    if int(size) <= 0 or float(ortho_width) <= 0.0:
        raise ValueError("Projection size and width must be positive.")
    min_x, min_y, max_x, max_y = pixel_bounds
    scale = float(ortho_width) / float(size)
    # Pixel edges are used instead of centers, providing the one-pixel-cell
    # conservative coverage required by the visual-bounds probe.
    u_min = (float(min_x) - float(size) * 0.5) * scale
    u_max = (float(max_x + 1) - float(size) * 0.5) * scale
    # Render-target rows run downwards, while the camera up vector runs upwards.
    v_min = (float(size) * 0.5 - float(max_y + 1)) * scale
    v_max = (float(size) * 0.5 - float(min_y)) * scale
    corners = [
        tuple(
            view_center[axis] + right[axis] * u + up[axis] * v
            for axis in range(3)
        )
        for u in (u_min, u_max)
        for v in (v_min, v_max)
    ]
    intervals: AxisIntervals = {}
    for axis in range(3):
        # The view's depth axis cannot be reconstructed from a silhouette.
        if abs(float(forward[axis])) >= 0.5:
            continue
        values = [corner[axis] for corner in corners]
        intervals[axis] = (min(values), max(values))
    return intervals


def combine_projection_intervals(
    projections: Iterable[AxisIntervals],
    minimum_views: int = 2,
) -> Bounds3D | None:
    samples = [dict(projection) for projection in projections if projection]
    if len(samples) < int(minimum_views):
        return None
    minimum = []
    maximum = []
    for axis in range(3):
        intervals = [sample[axis] for sample in samples if axis in sample]
        if not intervals:
            return None
        minimum.append(min(interval[0] for interval in intervals))
        maximum.append(max(interval[1] for interval in intervals))
    bounds = Bounds3D(tuple(minimum), tuple(maximum))
    if bounds.radius <= 1.0e-4 or not math.isfinite(bounds.radius):
        return None
    return bounds


def inflate_bounds(
    bounds: Bounds3D,
    absolute_padding: float,
    fractional_padding: float = 0.02,
) -> Bounds3D:
    minimum = []
    maximum = []
    for low, high in zip(bounds.minimum, bounds.maximum):
        span = max(0.0, high - low)
        padding = max(0.0, float(absolute_padding)) + span * max(
            0.0, float(fractional_padding)
        )
        minimum.append(low - padding)
        maximum.append(high + padding)
    return Bounds3D(tuple(minimum), tuple(maximum))


def next_probe_frame(
    current_center: Vector3,
    current_width: float,
    candidate: Bounds3D,
    *,
    edge_touched: bool,
    pass_index: int,
    maximum_passes: int = 3,
    framing_margin: float = 1.25,
) -> tuple[Vector3, float] | None:
    """Choose an expanded or tighter frame, or accept the current candidate."""
    if int(pass_index) + 1 >= int(maximum_passes):
        return None
    if edge_touched:
        return tuple(current_center), max(1.0e-3, float(current_width) * 2.0)
    desired_width = max(1.0e-3, candidate.maximum_span * float(framing_margin))
    center_shift = math.sqrt(
        sum(
            (candidate.center[index] - current_center[index]) ** 2
            for index in range(3)
        )
    )
    pixel_size = float(current_width) / 256.0
    if (
        float(current_width) > desired_width * 1.10
        or desired_width > float(current_width)
        or center_shift > pixel_size
    ):
        return candidate.center, desired_width
    return None


# ============================================================================
# Pixel Analysis
# ============================================================================

def probe_visible_alpha_bounds(
    pixels: Any,
    capture_size: int,
    threshold: float = 0.01,
):
    """Return visible bounds from the inverted HDR alpha capture."""
    min_x = min_y = int(capture_size)
    max_x = max_y = -1
    for index, pixel in enumerate(pixels or []):
        if 1.0 - float(pixel.a) <= float(threshold):
            continue
        x = index % capture_size
        y = index // capture_size
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None
    return min_x, min_y, max_x, max_y


# ============================================================================
# Image Ops
# ============================================================================


class ImageError(RuntimeError):
    pass


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _linear_to_srgb(value: float) -> float:
    value = _clamp01(value)
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * math.pow(value, 1.0 / 2.4) - 0.055


def pixels_to_image(
    pixels: Any,
    output_size: int,
    supersample: int,
    color_pixels: Any | None = None,
) -> tuple[Image.Image, dict]:
    """Convert UEFN HDR pixels into a straight-alpha Pillow image.

    ``pixels`` always supplies inverse opacity. Studio captures may supply a
    separate tone-curved RGB buffer because Unreal's final-color capture
    sources do not preserve alpha.
    """
    output_size = int(output_size)
    supersample = int(supersample)
    capture_size = output_size * supersample
    expected = capture_size * capture_size
    if pixels is None or len(pixels) != expected:
        actual = 0 if pixels is None else len(pixels)
        raise ImageError("Render Target returned %d pixels; expected %d." % (actual, expected))
    if color_pixels is not None and len(color_pixels) != expected:
        raise ImageError(
            "Tone-curved Render Target returned %d pixels; expected %d."
            % (len(color_pixels), expected)
        )

    rgba = bytearray(output_size * output_size * 4)
    sample_count = supersample * supersample
    alpha_nonzero = 0
    alpha_fractional = 0

    for y in range(output_size):
        source_y = y * supersample
        for x in range(output_size):
            source_x = x * supersample
            red = green = blue = alpha_sum = 0.0
            for sy in range(supersample):
                row = (source_y + sy) * capture_size + source_x
                for sx in range(supersample):
                    pixel = pixels[row + sx]
                    color_pixel = (
                        color_pixels[row + sx]
                        if color_pixels is not None
                        else pixel
                    )
                    alpha = _clamp01(1.0 - float(pixel.a))
                    alpha_sum += alpha
                    red += max(0.0, float(color_pixel.r))
                    green += max(0.0, float(color_pixel.g))
                    blue += max(0.0, float(color_pixel.b))

            alpha = alpha_sum / sample_count
            if alpha > 1.0e-6:
                red = (red / sample_count) / alpha
                green = (green / sample_count) / alpha
                blue = (blue / sample_count) / alpha
            else:
                red = green = blue = 0.0

            index = (y * output_size + x) * 4
            rgba[index] = int(_linear_to_srgb(red) * 255.0 + 0.5)
            rgba[index + 1] = int(_linear_to_srgb(green) * 255.0 + 0.5)
            rgba[index + 2] = int(_linear_to_srgb(blue) * 255.0 + 0.5)
            alpha_byte = int(alpha * 255.0 + 0.5)
            rgba[index + 3] = alpha_byte
            if alpha_byte:
                alpha_nonzero += 1
            if 0 < alpha_byte < 255:
                alpha_fractional += 1

    return Image.frombytes("RGBA", (output_size, output_size), bytes(rgba)), {
        "alpha_nonzero": alpha_nonzero,
        "alpha_fractional": alpha_fractional,
    }


def visible_alpha_bounds(pixels: Any, capture_size: int, threshold: float = 0.01):
    min_x = min_y = capture_size
    max_x = max_y = -1
    for index, pixel in enumerate(pixels or []):
        if 1.0 - float(pixel.a) <= threshold:
            continue
        x = index % capture_size
        y = index // capture_size
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None
    return min_x, min_y, max_x, max_y


def color_visible_alpha_bounds(pixels: Any, capture_size: int, threshold: int = 2):
    min_x = min_y = capture_size
    max_x = max_y = -1
    for index, pixel in enumerate(pixels or []):
        if 255 - int(pixel.a) <= threshold:
            continue
        x = index % capture_size
        y = index // capture_size
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return None
    return min_x, min_y, max_x, max_y


def sampled_luminance_stats(
    alpha_pixels: Any,
    color_pixels: Any,
    *,
    raw: bool,
    max_samples: int = 65536,
) -> dict[str, float | int]:
    """Measure visible tone-curved RGB without scanning a full-size export."""
    if alpha_pixels is None or color_pixels is None:
        raise ImageError("Highlight analysis requires alpha and color pixels.")
    if len(alpha_pixels) != len(color_pixels):
        raise ImageError("Highlight alpha/color buffers have different sizes.")
    count = len(alpha_pixels)
    if not count:
        return {
            "samples": 0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "white_fraction": 0.0,
        }
    step = max(1, int(math.ceil(count / max(1, int(max_samples)))))
    luminance = []
    for index in range(0, count, step):
        alpha_pixel = alpha_pixels[index]
        color_pixel = color_pixels[index]
        if raw:
            alpha = _clamp01(1.0 - float(alpha_pixel.a))
            if alpha <= 0.01:
                continue
            red = _linear_to_srgb(max(0.0, float(color_pixel.r)) / alpha) * 255.0
            green = _linear_to_srgb(max(0.0, float(color_pixel.g)) / alpha) * 255.0
            blue = _linear_to_srgb(max(0.0, float(color_pixel.b)) / alpha) * 255.0
        else:
            if 255 - int(alpha_pixel.a) <= 2:
                continue
            red = float(color_pixel.r)
            green = float(color_pixel.g)
            blue = float(color_pixel.b)
        luminance.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    if not luminance:
        return {
            "samples": 0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "white_fraction": 0.0,
        }
    luminance.sort()

    def percentile(fraction: float) -> float:
        index = int((len(luminance) - 1) * fraction)
        return round(float(luminance[index]), 3)

    return {
        "samples": len(luminance),
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "white_fraction": round(
            sum(value >= 250.0 for value in luminance) / len(luminance), 6
        ),
    }


def colors_to_preview_image(
    pixels: Any,
    size: int,
    color_pixels: Any | None = None,
) -> tuple[Image.Image, dict]:
    """Fast 8-bit conversion used only by the dirty-only interactive preview."""
    expected = int(size) * int(size)
    if pixels is None or len(pixels) != expected:
        raise ImageError("Preview read returned an invalid pixel count.")
    if color_pixels is not None and len(color_pixels) != expected:
        raise ImageError("Preview color read returned an invalid pixel count.")
    rgba = bytearray(expected * 4)
    nonzero = fractional = 0
    for index, pixel in enumerate(pixels):
        color_pixel = color_pixels[index] if color_pixels is not None else pixel
        target = index * 4
        alpha = 255 - int(pixel.a)
        rgba[target] = int(color_pixel.r)
        rgba[target + 1] = int(color_pixel.g)
        rgba[target + 2] = int(color_pixel.b)
        rgba[target + 3] = alpha
        if alpha:
            nonzero += 1
        if 0 < alpha < 255:
            fractional += 1
    return Image.frombytes("RGBA", (int(size), int(size)), bytes(rgba)), {
        "alpha_nonzero": nonzero,
        "alpha_fractional": fractional,
    }


def alpha_bounds_in_frame(bounds, capture_size: int, padding: float = 0.01) -> bool:
    if not bounds:
        return False
    inset = max(1, int(capture_size * padding))
    min_x, min_y, max_x, max_y = bounds
    return min_x >= inset and min_y >= inset and max_x < capture_size - inset and max_y < capture_size - inset


def outer_silhouette_mask(
    alpha: Image.Image,
    *,
    threshold: int = 2,
) -> Image.Image:
    """Return a binary alpha mask with only border-connected background removed.

    A one-pixel transparent border supplies a guaranteed exterior seed. The
    scanline flood fill uses 8-connectivity so diagonal openings remain part of
    the exterior rather than becoming accidental holes.
    """
    alpha = alpha.convert("L")
    width, height = alpha.size
    if width <= 0 or height <= 0:
        return Image.new("L", alpha.size, 0)

    threshold = max(0, min(254, int(threshold)))
    padded_width = width + 2
    padded_height = height + 2
    binary = alpha.point(lambda value: 1 if value > threshold else 0)
    padded = Image.new("L", (padded_width, padded_height), 0)
    padded.paste(binary, (1, 1))
    occupied = bytearray(padded.tobytes())

    exterior = bytearray(len(occupied))
    pending = deque([(0, 0)])
    while pending:
        seed_x, y = pending.pop()
        row = y * padded_width
        seed = row + seed_x
        if occupied[seed] or exterior[seed]:
            continue

        left = seed_x
        while left > 0:
            candidate = row + left - 1
            if occupied[candidate] or exterior[candidate]:
                break
            left -= 1
        right = seed_x
        while right + 1 < padded_width:
            candidate = row + right + 1
            if occupied[candidate] or exterior[candidate]:
                break
            right += 1
        exterior[row + left : row + right + 1] = b"\x01" * (
            right - left + 1
        )

        for adjacent_y in (y - 1, y + 1):
            if adjacent_y < 0 or adjacent_y >= padded_height:
                continue
            adjacent_row = adjacent_y * padded_width
            x = max(0, left - 1)
            limit = min(padded_width - 1, right + 1)
            while x <= limit:
                candidate = adjacent_row + x
                while x <= limit and (
                    occupied[candidate] or exterior[candidate]
                ):
                    x += 1
                    candidate += 1
                if x > limit:
                    break
                pending.append((x, adjacent_y))
                while x <= limit and not (
                    occupied[adjacent_row + x]
                    or exterior[adjacent_row + x]
                ):
                    x += 1

    exterior_image = Image.frombytes(
        "L",
        (padded_width, padded_height),
        bytes(exterior),
    ).crop((1, 1, width + 1, height + 1))
    return exterior_image.point(lambda value: 0 if value else 255)


def outer_silhouette_png_bytes(alpha: Image.Image) -> bytes:
    """Encode a filled silhouette as an opaque transient RGBA PNG."""
    mask = outer_silhouette_mask(alpha)
    opaque = Image.new("L", mask.size, 255)
    rgba = Image.merge("RGBA", (mask, mask, mask, opaque))
    buffer = BytesIO()
    rgba.save(buffer, "PNG", optimize=False, compress_level=1)
    return buffer.getvalue()


def outer_silhouette_png_from_inverse_alpha_pixels(
    pixels: Any,
    size: int,
) -> bytes:
    """Encode UEFN's inverted-alpha color buffer as a filled mask PNG."""
    size = int(size)
    expected = size * size
    if pixels is None or len(pixels) != expected:
        actual = 0 if pixels is None else len(pixels)
        raise ImageError(
            "Outer silhouette preview returned %d pixels; expected %d."
            % (actual, expected)
        )
    alpha = bytes(
        max(0, min(255, 255 - int(pixel.a)))
        for pixel in pixels
    )
    return outer_silhouette_png_bytes(
        Image.frombytes("L", (size, size), alpha)
    )


def _outer_outline_ring(alpha: Image.Image, width: int) -> Image.Image:
    silhouette = outer_silhouette_mask(alpha)
    expanded = silhouette.filter(ImageFilter.MaxFilter(width * 2 + 1))
    ring = ImageChops.subtract(expanded, silhouette)
    if ring.getbbox():
        # Soften the binary contour while keeping every pixel inside the
        # filled silhouette clear. The source image supplies its own AA edge.
        softened = ring.filter(ImageFilter.GaussianBlur(0.5))
        ring = ImageChops.multiply(softened, ImageChops.invert(silhouette))
    return ring


def apply_adjustments(image: Image.Image, state: AdjustState) -> Image.Image:
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")

    if abs(float(state.hue)) > 0.001:
        hsv = rgb.convert("HSV")
        h, s, v = hsv.split()
        shift = int((float(state.hue) % 360.0) * 255.0 / 360.0)
        h = h.point(lambda value: (value + shift) % 256)
        rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
    if abs(float(state.saturation) - 1.0) > 0.001:
        rgb = ImageEnhance.Color(rgb).enhance(max(0.0, float(state.saturation)))
    brightness = max(0.0, float(state.brightness)) * math.pow(2.0, float(state.exposure))
    if abs(brightness - 1.0) > 0.001:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if abs(float(state.contrast) - 1.0) > 0.001:
        rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, float(state.contrast)))

    adjusted = Image.merge("RGBA", (*rgb.split(), alpha))
    width = max(0, int(state.outline_width))
    if width:
        if bool(state.outer_silhouette_only):
            ring = _outer_outline_ring(alpha, width)
        else:
            kernel = width * 2 + 1
            expanded = alpha.filter(ImageFilter.MaxFilter(kernel))
            ring = ImageChops.subtract(expanded, alpha)
        color = tuple(max(0, min(255, int(v))) for v in state.outline_color)
        ring = ring.point(lambda value: int(value * color[3] / 255.0))
        outline = Image.new("RGBA", image.size, color[:3] + (0,))
        outline.putalpha(ring)
        adjusted = Image.alpha_composite(outline, adjusted)
    return adjusted


def composite_background(image: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    background = Image.new("RGBA", image.size, tuple(int(v) for v in color))
    return Image.alpha_composite(background, image)


def save_png(
    image: Image.Image,
    path: str,
    *,
    transparent: bool = True,
    background_color: tuple[int, int, int, int] = (32, 32, 32, 255),
) -> str:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    output = image if transparent else composite_background(image, background_color)
    output.save(path, "PNG", optimize=False, compress_level=6)
    if not os.path.isfile(path):
        raise ImageError("Pillow returned without creating the PNG.")
    return path


# ============================================================================
# Lighting
# ============================================================================

BASE_INTENSITY_LUMENS = 187.5
REFERENCE_RADIUS = 100.0
STUDIO_HIGHLIGHT_TRIGGER = 250.0
STUDIO_HIGHLIGHT_TARGET = 245.0
STUDIO_HIGHLIGHT_MIN_SCALE = 0.1


def disabled_capture_show_flags(studio: bool) -> tuple[str, ...]:
    """Return deterministic show flags for object/viewport capture."""
    disabled = [
        "Atmosphere",
        "Fog",
        "VolumetricFog",
        "Cloud",
        "MotionBlur",
        "EyeAdaptation",
    ]
    if studio:
        disabled.extend(("SkyLighting", "GlobalIllumination"))
    return tuple(disabled)


def studio_highlights_need_protection(stats: dict) -> bool:
    return bool(stats.get("samples")) and (
        float(stats.get("p95", 0.0)) >= STUDIO_HIGHLIGHT_TRIGGER
        or float(stats.get("white_fraction", 0.0)) >= 0.02
    )


def interpolate_studio_highlight_scale(
    base_scale: float,
    base_p95: float,
    low_scale: float,
    low_p95: float,
    target: float = STUDIO_HIGHLIGHT_TARGET,
) -> float:
    """Interpolate light energy logarithmically between two measured renders."""
    base_scale = max(1.0e-4, float(base_scale))
    low_scale = max(1.0e-4, min(base_scale, float(low_scale)))
    base_p95 = float(base_p95)
    low_p95 = float(low_p95)
    target = float(target)
    if base_p95 <= target:
        return base_scale
    if low_p95 >= target or base_p95 <= low_p95 + 1.0e-4:
        return low_scale
    amount = max(0.0, min(1.0, (target - low_p95) / (base_p95 - low_p95)))
    return math.exp(
        math.log(low_scale)
        + (math.log(base_scale) - math.log(low_scale)) * amount
    )


@dataclass(frozen=True)
class ResolvedStudioLight:
    role: str
    position: tuple[float, float, float]
    intensity: float
    temperature_kelvin: int
    size: float
    cast_shadows: bool


STUDIO_PRESETS = {
    "neutral": StudioRigState(),
    "soft": StudioRigState(
        key=StudioLightState(0.75, 0, 2.8, (2.8, -1.8, 2.2), True),
        fill=StudioLightState(0.50, 200, 3.2, (2.4, 2.0, 0.8), False),
        rim=StudioLightState(0.35, 500, 2.0, (-2.2, 0.3, 2.0), False),
    ),
    "dramatic": StudioRigState(
        key=StudioLightState(1.25, -1200, 0.8, (2.8, -1.8, 2.2), True),
        fill=StudioLightState(0.12, 0, 1.5, (2.4, 2.0, 0.8), False),
        rim=StudioLightState(0.90, 1800, 0.8, (-2.2, 0.3, 2.0), True),
    ),
    "flat": StudioRigState(
        key=StudioLightState(0.60, 0, 3.5, (2.8, -1.8, 2.2), False),
        fill=StudioLightState(0.60, 0, 3.5, (2.4, 2.0, 0.8), False),
        rim=StudioLightState(0.25, 0, 2.5, (-2.2, 0.3, 2.0), False),
    ),
}


def get_builtin_studio_rig(preset_id: str) -> StudioRigState:
    preset = STUDIO_PRESETS.get(str(preset_id or "").lower())
    if preset is None:
        preset = STUDIO_PRESETS["neutral"]
    return StudioRigState.from_dict(preset.to_dict())


class LightingIsolationError(RuntimeError):
    pass


def resolve_studio_lights(
    state: LightingState,
    bounds_radius: float,
    intensity_scale: float = 1.0,
) -> tuple[ResolvedStudioLight, ...]:
    """Resolve a stable preset into subject-scaled light parameters."""
    state = LightingState.from_dict(state.to_dict())
    rig = (
        StudioRigState.from_dict(state.rig.to_dict())
        if state.rig is not None
        else get_builtin_studio_rig(state.preset)
    )
    radius = max(1.0e-3, float(bounds_radius))
    base = (
        BASE_INTENSITY_LUMENS
        * (radius / REFERENCE_RADIUS) ** 2
        * state.intensity
        * max(0.0, float(intensity_scale))
    )
    resolved = []
    for role in STUDIO_LIGHT_ROLES:
        light = getattr(rig, role)
        resolved.append(
            ResolvedStudioLight(
                role=role,
                position=tuple(value * radius for value in light.position),
                intensity=base * light.intensity_multiplier,
                temperature_kelvin=max(
                    1000,
                    min(
                        15000,
                        state.temperature_kelvin + light.temperature_offset,
                    ),
                ),
                size=radius * light.size_multiplier,
                cast_shadows=light.cast_shadows,
            )
        )
    return tuple(resolved)


def _read_channels(component) -> tuple[bool, bool, bool]:
    channels = component.get_editor_property("lighting_channels")
    return tuple(
        bool(channels.get_editor_property("channel%d" % index))
        for index in range(3)
    )


@contextmanager
def isolate_lighting_channels(components: Iterable):
    """Move components to channel 2 and always restore their exact prior state."""
    snapshots = []
    seen = set()
    active_error = None
    try:
        for component in components:
            if component is None or id(component) in seen:
                continue
            seen.add(id(component))
            channels = _read_channels(component)
            snapshots.append((component, channels))
            component.set_lighting_channels(False, False, True)
        yield len(snapshots)
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        restore_errors = []
        for component, channels in reversed(snapshots):
            try:
                component.set_lighting_channels(*channels)
            except Exception as exc:
                restore_errors.append(str(exc))
        if restore_errors:
            message = "Lighting Channel restoration failed: %s" % "; ".join(
                restore_errors
            )
            if active_error is not None and hasattr(active_error, "add_note"):
                active_error.add_note(message)
            elif active_error is None:
                raise LightingIsolationError(message)


# ============================================================================
# Sources
# ============================================================================

SUPPORTED_CLASS_NAMES = {
    "StaticMesh": SourceKind.STATIC_MESH,
    "SkeletalMesh": SourceKind.SKELETAL_MESH,
    "Blueprint": SourceKind.BLUEPRINT,
    "BlueprintGeneratedClass": SourceKind.BLUEPRINT,
    "NiagaraSystem": SourceKind.NIAGARA,
}


def source_from_asset(asset) -> CaptureSource | None:
    if asset is None:
        return None
    class_name = asset.get_class().get_name()
    kind = SUPPORTED_CLASS_NAMES.get(class_name)
    if kind is None:
        if isinstance(asset, unreal.StaticMesh):
            kind = SourceKind.STATIC_MESH
        elif isinstance(asset, unreal.SkeletalMesh):
            kind = SourceKind.SKELETAL_MESH
        elif isinstance(asset, unreal.NiagaraSystem):
            kind = SourceKind.NIAGARA
        elif hasattr(asset, "generated_class"):
            kind = SourceKind.BLUEPRINT
    if kind is None:
        return None
    if kind == SourceKind.BLUEPRINT:
        generated = asset.generated_class() if hasattr(asset, "generated_class") else asset
        try:
            default_object = unreal.get_default_object(generated)
        except Exception:
            return None
        if not isinstance(default_object, unreal.Actor):
            return None
    return CaptureSource(kind=kind, paths=[asset.get_path_name()], display_name=asset.get_name())


def selected_asset_sources() -> list[CaptureSource]:
    sources = []
    for asset in unreal.EditorUtilityLibrary.get_selected_assets():
        source = source_from_asset(asset)
        if source:
            sources.append(source)
    return sources


def selected_actor_source() -> CaptureSource | None:
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = list(actor_sub.get_selected_level_actors()) if actor_sub else []
    if not actors:
        return None
    return CaptureSource(
        kind=SourceKind.ACTORS,
        paths=[actor.get_path_name() for actor in actors],
        display_name="%d selected actors" % len(actors),
    )


def _content_browser_internal_path(path) -> str:
    """Convert a Content Browser virtual path into an Asset Registry path."""
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        item_path = unreal.ContentBrowserItemPath()
        item_path.set_path(value, unreal.ContentBrowserPathType.VIRTUAL)
        internal = str(item_path.get_internal_path())
        if internal:
            return internal
    except Exception:
        pass
    # UEFN's selected-folder APIs commonly return paths rooted at ``/All``.
    # AssetRegistry expects the mount path without that virtual root.
    if value == "/All":
        return "/"
    if value.startswith("/All/"):
        return value[len("/All") :]
    return value


def selected_folder_sources(recursive: bool = True) -> tuple[list[CaptureSource], list[str]]:
    folders = []
    getter = getattr(unreal.EditorUtilityLibrary, "get_selected_folder_paths", None)
    if getter:
        try:
            folders = [str(path) for path in getter()]
        except Exception:
            folders = []
    if not folders:
        getter = getattr(unreal.EditorUtilityLibrary, "get_selected_path_view_folder_paths", None)
        if getter:
            try:
                folders = [str(path) for path in getter()]
            except Exception:
                folders = []

    folders = list(
        dict.fromkeys(
            internal
            for internal in (
                _content_browser_internal_path(folder) for folder in folders
            )
            if internal
        )
    )

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    sources: list[CaptureSource] = []
    ignored: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        for data in registry.get_assets_by_path(folder, recursive=recursive):
            package = str(data.package_name)
            if package in seen:
                continue
            seen.add(package)
            asset = unreal.EditorAssetLibrary.load_asset(package)
            source = source_from_asset(asset)
            if source:
                sources.append(source)
            else:
                ignored.append("%s (%s)" % (package, str(data.asset_class_path.asset_name)))
    return sources, ignored


def supported_sources_from_assets(assets: Iterable) -> tuple[list[CaptureSource], list[str]]:
    sources = []
    ignored = []
    for asset in assets:
        source = source_from_asset(asset)
        if source:
            sources.append(source)
        elif asset is not None:
            ignored.append("%s (%s)" % (asset.get_path_name(), asset.get_class().get_name()))
    return sources, ignored


# ============================================================================
# Importer
# ============================================================================

class TextureImportError(RuntimeError):
    pass


def _set_texture_ui_properties(texture) -> None:
    settings = (
        ("lod_group", unreal.TextureGroup.TEXTUREGROUP_UI),
        ("srgb", True),
        ("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT),
        ("mip_gen_settings", unreal.TextureMipGenSettings.TMGS_FROM_TEXTURE_GROUP),
    )
    for name, value in settings:
        try:
            texture.set_editor_property(name, value)
        except Exception as exc:
            unreal.log_warning("[ThumbnailCreator] Texture property %s: %s" % (name, exc))


def import_or_reimport_texture(
    png_path: str,
    destination_path: str,
    asset_name: str | None = None,
) -> dict:
    """Import with replace_existing so the existing package/object keeps its references."""
    png_path = os.path.abspath(png_path)
    if not os.path.isfile(png_path):
        raise TextureImportError("PNG is inaccessible: %s" % png_path)
    content_root = detect_project_content_root(unreal)
    try:
        destination_path = validate_destination_path(destination_path, content_root)
    except ContentPathError as exc:
        raise TextureImportError(str(exc)) from exc
    asset_name = safe_name(asset_name or os.path.splitext(os.path.basename(png_path))[0])
    object_path = "%s/%s" % (destination_path, asset_name)
    asset_lib = unreal.EditorAssetLibrary
    asset_lib.make_directory(destination_path)
    before = asset_lib.load_asset(object_path)

    task = unreal.AssetImportTask()
    task.set_editor_property("automated", True)
    task.set_editor_property("filename", png_path)
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("destination_name", asset_name)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("replace_existing_settings", False)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    texture = asset_lib.load_asset(object_path)
    if texture is None or not isinstance(texture, unreal.Texture2D):
        imported = [str(path) for path in task.get_objects()] if hasattr(task, "get_objects") else []
        raise TextureImportError("Texture import failed for %s (objects: %s)." % (object_path, imported))
    _set_texture_ui_properties(texture)
    texture.modify()
    asset_lib.save_loaded_asset(texture, False)
    after_path = texture.get_path_name()
    return {
        "texture_path": after_path,
        "asset_path": object_path,
        "reimported": before is not None,
        "same_object": bool(before is None or before == texture),
        "source_png": png_path,
        "content_root": content_root,
        "lod_group": str(texture.get_editor_property("lod_group")),
        "srgb": bool(texture.get_editor_property("srgb")),
        "compression": str(texture.get_editor_property("compression_settings")),
        "mips": str(texture.get_editor_property("mip_gen_settings")),
    }


# ============================================================================
# Slate State
# ============================================================================

SLATE_SCHEMA_VERSION = 3
SLATE_STORE_FOLDER = "slate_v3"
PREVIEW_BACKGROUNDS = ("Checker", "Dark", "Light", "Solid")
TARGET_PREVIEW_FPS = 30.0
PREVIEW_FRAME_INTERVAL = 1.0 / TARGET_PREVIEW_FPS
PREVIEW_INTERACTION_TAIL = 0.35
INSPECTOR_TABS = ("Camera", "Lighting", "Look", "Output")


def default_slate_v3_directory() -> str:
    return os.path.join(default_saved_directory(), SLATE_STORE_FOLDER)


class SlateV3Store(ThumbnailCreatorStore):
    """Fresh namespace for SlateIM data; legacy JSON files remain untouched."""

    def __init__(self, root: str | None = None):
        self.json = JsonStore(
            root or default_slate_v3_directory(),
            schema_version=SLATE_SCHEMA_VERSION,
        )


@dataclass
class ThumbnailCreatorUIState:
    """Persistent preferences used by the live Slate workspace."""

    diagnostics_visible: bool = False
    overlay_thirds: bool = False
    overlay_center: bool = False
    safe_frame: int = 0
    comparison_bypass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ThumbnailCreatorUIState":
        payload = dict(data) if isinstance(data, dict) else {}
        try:
            safe_frame = int(payload.get("safe_frame", 0))
        except (TypeError, ValueError):
            safe_frame = 0
        return cls(
            diagnostics_visible=bool(
                payload.get("diagnostics_visible", False)
            ),
            overlay_thirds=bool(payload.get("overlay_thirds", False)),
            overlay_center=bool(payload.get("overlay_center", False)),
            safe_frame=safe_frame if safe_frame in (0, 80, 90) else 0,
            comparison_bypass=bool(
                payload.get("comparison_bypass", False)
            ),
        )


@dataclass
class ThumbnailCreatorState:
    source: CaptureSource | None = None
    camera: CameraState = field(default_factory=CameraState)
    adjust: AdjustState = field(default_factory=AdjustState)
    lighting: LightingState = field(default_factory=LightingState)
    export: ExportOptions = field(default_factory=ExportOptions)
    preview_background: str = "Checker"
    live_preview: bool = False
    ui: ThumbnailCreatorUIState = field(default_factory=ThumbnailCreatorUIState)

    def capture_request(
        self,
        *,
        preview: bool = False,
        source: CaptureSource | None = None,
    ) -> CaptureRequest:
        request_source = source if source is not None else self.source
        if request_source is None:
            raise ValueError("Select an asset, level actors, or Whole View first.")
        return CaptureRequest(
            source=copy.deepcopy(request_source),
            camera=copy.deepcopy(self.camera),
            adjust=copy.deepcopy(self.adjust),
            lighting=copy.deepcopy(self.lighting),
            export=copy.deepcopy(self.export),
            preview=bool(preview),
            preview_fast=bool(preview),
        )

    def to_dict(self) -> dict[str, Any]:
        request = None
        if self.source is not None:
            request = self.capture_request(preview=False).to_dict()
        return {
            "request": request,
            # Preserve editor settings even when no source is selected.
            "camera": asdict(self.camera),
            "adjust": asdict(self.adjust),
            "lighting": self.lighting.to_dict(),
            "export": asdict(self.export),
            "preview_background": self.preview_background,
            "live_preview": self.live_preview,
            "ui": self.ui.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        default_export: ExportOptions | None = None,
    ) -> "ThumbnailCreatorState":
        payload = dict(data) if isinstance(data, dict) else {}
        request_data = payload.get("request")
        if isinstance(request_data, dict):
            request = CaptureRequest.from_dict(request_data)
            state = cls(
                source=request.source,
                camera=request.camera,
                adjust=request.adjust,
                lighting=request.lighting,
                export=request.export,
            )
        else:
            camera_data = dict(payload.get("camera") or {})
            adjust_data = dict(payload.get("adjust") or {})
            lighting_data = payload.get("lighting")
            export_data = dict(payload.get("export") or {})
            if "outline_color" in adjust_data:
                adjust_data["outline_color"] = tuple(adjust_data["outline_color"])
            if "background_color" in export_data:
                export_data["background_color"] = tuple(
                    export_data["background_color"]
                )
            state = cls(
                camera=CameraState(**camera_data),
                adjust=AdjustState(**adjust_data),
                lighting=LightingState.from_dict(lighting_data),
                export=(
                    ExportOptions(**export_data)
                    if export_data
                    else copy.deepcopy(default_export or ExportOptions())
                ),
            )
        background = str(payload.get("preview_background") or "Checker")
        state.preview_background = (
            background if background in PREVIEW_BACKGROUNDS else "Checker"
        )
        state.live_preview = bool(payload.get("live_preview", False))
        state.ui = ThumbnailCreatorUIState.from_dict(payload.get("ui"))
        return state


@dataclass
class BatchRow:
    source: CaptureSource
    checked: bool = True
    status: str = "pending"
    error: str = ""
    result: Any = None


@dataclass
class PreviewDiagnostics:
    target_fps: float = 30.0
    measured_fps: float = 0.0
    capture_scene_ms: float = 0.0
    capture_frames: int = 0
    active: bool = False
    live: bool = False
    suspended: bool = False
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_fps": self.target_fps,
            "measured_fps": self.measured_fps,
            "capture_scene_ms": self.capture_scene_ms,
            "capture_frames": self.capture_frames,
            "active": self.active,
            "live": self.live,
            "suspended": self.suspended,
            "last_error": self.last_error,
        }


@dataclass
class PreviewScheduler:
    """Fixed-deadline 30 FPS scheduler with an adaptive idle tail."""

    interval: float = PREVIEW_FRAME_INTERVAL
    interaction_tail: float = PREVIEW_INTERACTION_TAIL
    next_capture_at: float = 0.0
    active_until: float = 0.0
    final_capture_pending: bool = False
    live: bool = False
    suspended: bool = False

    def mark_dirty(self, now: float) -> None:
        was_active = self.is_active(now)
        self.active_until = max(
            float(self.active_until),
            float(now) + float(self.interaction_tail),
        )
        self.final_capture_pending = True
        if not was_active:
            self.next_capture_at = 0.0

    def set_live(self, enabled: bool, now: float) -> None:
        self.live = bool(enabled)
        if self.live:
            self.mark_dirty(now)

    def is_active(self, now: float) -> bool:
        return bool(
            not self.suspended
            and (
                self.live
                or float(now) <= float(self.active_until)
                or self.final_capture_pending
            )
        )

    def is_due(self, now: float) -> bool:
        now = float(now)
        if not self.is_active(now):
            return False
        deadline = float(self.next_capture_at)
        if deadline > 0.0 and now < deadline:
            return False
        if deadline <= 0.0:
            deadline = now
        while deadline <= now:
            deadline += float(self.interval)
        self.next_capture_at = deadline
        return True

    def did_capture(self, now: float) -> None:
        if not self.live and float(now) >= float(self.active_until):
            self.final_capture_pending = False

    def suspend(self, enabled: bool) -> None:
        self.suspended = bool(enabled)
        if not self.suspended:
            self.next_capture_at = 0.0


# ============================================================================
# Capture
# ============================================================================

TEMP_Z = -500000.0
MAX_CAPTURE_SIZE = 4096
TEMP_PREFIX = "__ThumbnailCreator_"
STUDIO_EXPOSURE_BIAS = 0.0
VISUAL_BOUNDS_PROBE_SIZE = 256
VISUAL_BOUNDS_MAX_PASSES = 3
VISUAL_BOUNDS_INITIAL_RADIUS = 100.0


class CaptureError(RuntimeError):
    pass


@dataclass
class CapturedFrame:
    image: object
    result: CaptureResult


class CaptureSession:
    """Persistent editor capture stage. All owned objects are transient and reusable."""

    def __init__(self):
        self.actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        self.level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.world = unreal.EditorLevelLibrary.get_editor_world()
        if self.actor_sub is None or self.world is None:
            raise CaptureError("Open an editable level before using the tool.")
        try:
            self.previous_selection = list(self.actor_sub.get_selected_level_actors())
        except Exception:
            self.previous_selection = []
        self.owned_source_actors: list = []
        self.source_actors: list = []
        self.capture_actor = None
        self.studio_lights: list = []
        self.render_target = None
        self.render_target_size = 0
        self.bounds_render_target = None
        self.source_key = ""
        self._prepared_source_signature = None
        self._actor_state_signature = None
        self.bounds_center = unreal.Vector(0.0, 0.0, TEMP_Z)
        self.bounds_extent = unreal.Vector(57.735, 57.735, 57.735)
        self.bounds_radius = 100.0
        self.coarse_bounds_center = self.bounds_center
        self.coarse_bounds_extent = self.bounds_extent
        self.coarse_bounds_radius = self.bounds_radius
        self._visual_bounds_dirty = True
        self._visual_bounds_method = "actor_fallback"
        self._visual_bounds_fallback = "not_probed"
        self._visual_bounds_probe_count = 0
        self._studio_highlight_scale_cache = {}
        self.viewport_override = None
        self.closed = False
        self._source_changed = False

    def _spawn_actor(self, actor_class, location=None, rotation=None):
        actor = self.actor_sub.spawn_actor_from_class(
            actor_class,
            location or unreal.Vector(0.0, 0.0, TEMP_Z),
            rotation or unreal.Rotator(),
            True,
        )
        if actor is None:
            raise CaptureError("UEFN could not spawn %s." % actor_class.get_name())
        return actor

    def _destroy_owned_sources(self):
        for actor in reversed(self.owned_source_actors):
            try:
                if actor is not None:
                    self.actor_sub.destroy_actor(actor)
            except Exception as exc:
                unreal.log_warning("[ThumbnailCreator] Source cleanup failed: %s" % exc)
        self.owned_source_actors = []
        self.source_actors = []

    def _find_actor(self, path: str):
        for actor in self.actor_sub.get_all_level_actors():
            if actor.get_path_name() == path or actor.get_name() == path or actor.get_actor_label() == path:
                return actor
        return None

    def _load_asset(self, path: str):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if asset is None:
            raise CaptureError("Asset not found: %s" % path)
        return asset

    @staticmethod
    def _vector_tuple(vector) -> tuple[float, float, float]:
        return float(vector.x), float(vector.y), float(vector.z)

    @staticmethod
    def _source_signature(source: CaptureSource):
        return source.key, round(float(source.niagara_time), 6)

    def _current_actor_state_signature(self):
        signature = []
        for actor in self.source_actors:
            try:
                location = actor.get_actor_location()
                rotation = actor.get_actor_rotation()
                scale = actor.get_actor_scale3d()
                center, extent = actor.get_actor_bounds(False, True)
                values = (
                    float(location.x), float(location.y), float(location.z),
                    float(rotation.pitch), float(rotation.yaw), float(rotation.roll),
                    float(scale.x), float(scale.y), float(scale.z),
                    float(center.x), float(center.y), float(center.z),
                    float(extent.x), float(extent.y), float(extent.z),
                )
                signature.append(
                    (actor.get_path_name(),) + tuple(round(value, 4) for value in values)
                )
            except Exception as exc:
                signature.append((str(actor), "unavailable", str(exc)))
        return tuple(signature)

    def _invalidate_visual_bounds(self, reason: str):
        self._visual_bounds_dirty = True
        self._visual_bounds_method = "actor_fallback"
        self._visual_bounds_fallback = str(reason or "invalidated")
        self._visual_bounds_probe_count = 0
        self.bounds_center = self.coarse_bounds_center
        self.bounds_extent = self.coarse_bounds_extent
        self.bounds_radius = self.coarse_bounds_radius

    def prepare_source(self, source: CaptureSource):
        if self.closed:
            raise CaptureError("The capture session is closed.")
        source_signature = self._source_signature(source)
        if source.key == self.source_key and self.source_actors:
            if source_signature == self._prepared_source_signature:
                actor_signature = self._current_actor_state_signature()
                if actor_signature != self._actor_state_signature:
                    self._update_bounds()
                    self._actor_state_signature = self._current_actor_state_signature()
                return
        self._destroy_owned_sources()
        self.source_key = source.key
        self._prepared_source_signature = source_signature
        self._source_changed = True
        stage = unreal.Vector(0.0, 0.0, TEMP_Z)

        if source.kind == SourceKind.WHOLE_VIEW:
            self.source_actors = []
            self._actor_state_signature = None
            self._visual_bounds_dirty = False
            self._visual_bounds_method = "viewport"
            self._visual_bounds_fallback = ""
            self.read_viewport_camera()
            return
        if not source.paths:
            raise CaptureError("The capture source is empty.")

        if source.kind == SourceKind.STATIC_MESH:
            asset = self._load_asset(source.paths[0])
            if not isinstance(asset, unreal.StaticMesh):
                raise CaptureError("The source is not a StaticMesh.")
            actor = self.actor_sub.spawn_actor_from_object(asset, stage, unreal.Rotator(), True)
            if actor is None:
                raise CaptureError("UEFN could not spawn the StaticMesh.")
            actor.set_actor_label(TEMP_PREFIX + "StaticMesh", False)
            self.source_actors = self.owned_source_actors = [actor]

        elif source.kind == SourceKind.SKELETAL_MESH:
            asset = self._load_asset(source.paths[0])
            if not isinstance(asset, unreal.SkeletalMesh):
                raise CaptureError("The source is not a SkeletalMesh.")
            actor = self._spawn_actor(unreal.SkeletalMeshActor, stage)
            actor.set_actor_label(TEMP_PREFIX + "SkeletalMesh", False)
            component = actor.skeletal_mesh_component
            component.set_skeletal_mesh_asset(asset)
            component.set_editor_property("pause_anims", True)
            self.source_actors = self.owned_source_actors = [actor]

        elif source.kind == SourceKind.BLUEPRINT:
            asset = self._load_asset(source.paths[0])
            class_name = asset.get_class().get_name()
            if class_name == "EntityPrefab":
                raise CaptureError(
                    "Entity Prefabs are not supported as thumbnail sources."
                )
            generated = asset.generated_class() if hasattr(asset, "generated_class") else asset
            try:
                actor = self._spawn_actor(generated, stage)
            except Exception as exc:
                raise CaptureError("The Blueprint/Class is not spawnable: %s" % exc) from exc
            actor.set_actor_label(TEMP_PREFIX + "Blueprint", False)
            self.source_actors = self.owned_source_actors = [actor]

        elif source.kind == SourceKind.ACTORS:
            actors = [self._find_actor(path) for path in source.paths]
            self.source_actors = [actor for actor in actors if actor is not None]
            if not self.source_actors:
                raise CaptureError("None of the selected actors still exists in the level.")

        elif source.kind == SourceKind.NIAGARA:
            asset = self._load_asset(source.paths[0])
            if not isinstance(asset, unreal.NiagaraSystem):
                raise CaptureError("The source is not a NiagaraSystem.")
            actor = self._spawn_actor(unreal.NiagaraActor, stage)
            actor.set_actor_label(TEMP_PREFIX + "Niagara", False)
            component = actor.get_component_by_class(unreal.NiagaraComponent)
            if component is None:
                raise CaptureError("The temporary NiagaraActor has no NiagaraComponent.")
            component.set_asset(asset, True)
            component.set_paused(False)
            component.activate(True)
            component.reinitialize_system()
            if source.niagara_time > 0.0:
                component.advance_simulation_by_time(float(source.niagara_time), 1.0 / 30.0)
            component.set_paused(True)
            self.source_actors = self.owned_source_actors = [actor]
        else:
            raise CaptureError("Unsupported source type: %s" % source.kind.value)

        self._update_bounds()
        self._actor_state_signature = self._current_actor_state_signature()

    def _update_bounds(self):
        if not self.source_actors:
            return
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        valid = 0
        for actor in self.source_actors:
            try:
                center, extent = actor.get_actor_bounds(False, True)
                values = (
                    float(center.x), float(center.y), float(center.z),
                    float(extent.x), float(extent.y), float(extent.z),
                )
                if not all(math.isfinite(value) for value in values):
                    continue
                if any(value < 0.0 for value in values[3:]):
                    continue
                min_x = min(min_x, float(center.x - extent.x))
                min_y = min(min_y, float(center.y - extent.y))
                min_z = min(min_z, float(center.z - extent.z))
                max_x = max(max_x, float(center.x + extent.x))
                max_y = max(max_y, float(center.y + extent.y))
                max_z = max(max_z, float(center.z + extent.z))
                valid += 1
            except Exception:
                pass
        fallback_reason = "source_bounds_changed"
        if valid:
            center = unreal.Vector(
                (min_x + max_x) * 0.5,
                (min_y + max_y) * 0.5,
                (min_z + max_z) * 0.5,
            )
            extent = unreal.Vector(
                (max_x - min_x) * 0.5,
                (max_y - min_y) * 0.5,
                (max_z - min_z) * 0.5,
            )
            radius = math.sqrt(
                float(extent.x) ** 2
                + float(extent.y) ** 2
                + float(extent.z) ** 2
            )
        else:
            radius = 0.0
        if radius <= 1.0e-4 or not math.isfinite(radius):
            locations = []
            for actor in self.source_actors:
                try:
                    locations.append(actor.get_actor_location())
                except Exception:
                    pass
            if not locations:
                raise CaptureError("The source has no usable bounds or location.")
            count = float(len(locations))
            center = unreal.Vector(
                sum(float(value.x) for value in locations) / count,
                sum(float(value.y) for value in locations) / count,
                sum(float(value.z) for value in locations) / count,
            )
            extent = unreal.Vector(
                VISUAL_BOUNDS_INITIAL_RADIUS,
                VISUAL_BOUNDS_INITIAL_RADIUS,
                VISUAL_BOUNDS_INITIAL_RADIUS,
            )
            radius = math.sqrt(3.0) * VISUAL_BOUNDS_INITIAL_RADIUS
            fallback_reason = "actor_bounds_invalid"
        self.coarse_bounds_center = center
        self.coarse_bounds_extent = extent
        self.coarse_bounds_radius = radius
        self._invalidate_visual_bounds(fallback_reason)
        self._source_changed = True

    def _ensure_capture_actor(self):
        if self.capture_actor is not None:
            return
        self.capture_actor = self._spawn_actor(unreal.SceneCapture2D)
        self.capture_actor.set_actor_label(TEMP_PREFIX + "Capture", False)
        component = self.capture_actor.capture_component2d
        component.capture_every_frame = False
        component.capture_on_movement = False
        component.always_persist_rendering_state = True
        component.capture_source = unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR
        self._set_capture_show_flags(False)

    def _set_capture_show_flags(self, studio: bool):
        if self.capture_actor is None:
            return
        self.capture_actor.capture_component2d.show_flag_settings = [
            unreal.EngineShowFlagsSetting(show_flag_name=name, enabled=False)
            for name in disabled_capture_show_flags(studio)
        ]

    def _configure_capture_exposure(self, studio: bool):
        component = self.capture_actor.capture_component2d
        settings = component.get_editor_property("post_process_settings")
        for override in (
            "override_auto_exposure_method",
            "override_auto_exposure_bias",
            "override_auto_exposure_apply_physical_camera_exposure",
        ):
            settings.set_editor_property(override, bool(studio))
        if studio:
            settings.set_editor_property(
                "auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL
            )
            settings.set_editor_property(
                "auto_exposure_bias", STUDIO_EXPOSURE_BIAS
            )
            settings.set_editor_property(
                "auto_exposure_apply_physical_camera_exposure", False
            )
        component.set_editor_property("post_process_settings", settings)
        component.set_editor_property("post_process_blend_weight", 1.0)

    def _source_primitive_components(self):
        components = []
        seen_components = set()
        seen_actors = set()
        actors = []
        for source_actor in self.source_actors:
            candidates = [source_actor]
            try:
                candidates.extend(source_actor.get_attached_actors(True, True))
            except Exception:
                # Some transient source types do not expose attachment queries.
                # Their own primitive components remain valid Studio targets.
                pass
            for actor in candidates:
                if actor is None or id(actor) in seen_actors:
                    continue
                seen_actors.add(id(actor))
                actors.append(actor)

        for actor in actors:
            try:
                actor_components = actor.get_components_by_class(unreal.PrimitiveComponent)
            except Exception:
                actor_components = []
            for component in actor_components:
                if component is None or id(component) in seen_components:
                    continue
                seen_components.add(id(component))
                if hasattr(component, "set_lighting_channels"):
                    components.append(component)
        if not components:
            raise CaptureError(
                "Studio lighting is not available for this source."
            )
        return components

    def _ensure_studio_lights(self):
        if len(self.studio_lights) == 3 and all(self.studio_lights):
            return
        self._destroy_studio_lights()
        created = []
        try:
            for role in ("Key", "Fill", "Rim"):
                actor = self._spawn_actor(unreal.RectLight)
                actor.set_actor_label(TEMP_PREFIX + "Light_" + role, False)
                component = actor.rect_light_component
                component.set_mobility(unreal.ComponentMobility.MOVABLE)
                component.set_editor_property(
                    "intensity_units", unreal.LightUnits.LUMENS
                )
                component.set_lighting_channels(False, False, True)
                component.set_indirect_lighting_intensity(0.0)
                component.set_affect_translucent_lighting(True)
                component.set_use_temperature(True)
                component.set_visibility(False, True)
                created.append(actor)
            self.studio_lights = created
        except Exception as exc:
            for actor in reversed(created):
                try:
                    self.actor_sub.destroy_actor(actor)
                except Exception:
                    pass
            self.studio_lights = []
            raise CaptureError("Studio lighting could not be created: %s" % exc) from exc

    def _destroy_studio_lights(self):
        for actor in reversed(self.studio_lights):
            try:
                if actor is not None:
                    self.actor_sub.destroy_actor(actor)
            except Exception as exc:
                unreal.log_warning("[ThumbnailCreator] Studio light cleanup failed: %s" % exc)
        self.studio_lights = []

    def _set_studio_lights_visible(self, visible: bool):
        errors = []
        for actor in self.studio_lights:
            try:
                actor.rect_light_component.set_visibility(bool(visible), True)
            except Exception as exc:
                errors.append(str(exc))
        if errors and visible:
            raise CaptureError(
                "Studio lights could not be activated: %s" % "; ".join(errors)
            )
        if errors:
            unreal.log_warning(
                "[ThumbnailCreator] Studio light hide failed: %s"
                % "; ".join(errors)
            )

    def _studio_highlight_cache_key(self, state, camera):
        return (
            self._prepared_source_signature,
            tuple(round(value, 3) for value in self._vector_tuple(self.bounds_center)),
            round(float(self.bounds_radius), 3),
            (
                round(float(camera.yaw), 1),
                round(float(camera.pitch), 1),
                round(float(camera.roll), 1),
                round(float(camera.dolly), 2),
                round(float(camera.fov), 1),
            ),
            json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")),
        )

    def _remember_studio_highlight_scale(self, key, scale: float):
        if key not in self._studio_highlight_scale_cache:
            while len(self._studio_highlight_scale_cache) >= 32:
                self._studio_highlight_scale_cache.pop(
                    next(iter(self._studio_highlight_scale_cache))
                )
        self._studio_highlight_scale_cache[key] = max(
            STUDIO_HIGHLIGHT_MIN_SCALE, min(1.0, float(scale))
        )

    def _configure_studio_lights(
        self,
        state,
        camera_rotation,
        intensity_scale: float = 1.0,
    ):
        self._ensure_studio_lights()
        resolved = resolve_studio_lights(
            state,
            self.bounds_radius,
            intensity_scale=intensity_scale,
        )
        forward = unreal.MathLibrary.get_forward_vector(camera_rotation)
        toward_camera = forward * -1.0
        right = unreal.MathLibrary.get_right_vector(camera_rotation)
        up = unreal.MathLibrary.get_up_vector(camera_rotation)
        attenuation = max(1000.0, float(self.bounds_radius) * 10.0)
        try:
            for actor, spec in zip(self.studio_lights, resolved):
                toward, lateral, vertical = spec.position
                position = (
                    self.bounds_center
                    + toward_camera * toward
                    + right * lateral
                    + up * vertical
                )
                rotation = unreal.MathLibrary.find_look_at_rotation(
                    position, self.bounds_center
                )
                actor.set_actor_location(position, False, False)
                actor.set_actor_rotation(rotation, False)
                component = actor.rect_light_component
                component.set_intensity(float(spec.intensity))
                component.set_temperature(float(spec.temperature_kelvin))
                component.set_source_width(float(spec.size))
                component.set_source_height(float(spec.size))
                component.set_attenuation_radius(attenuation)
                component.set_cast_shadows(bool(spec.cast_shadows))
        except Exception as exc:
            raise CaptureError("Studio lighting configuration failed: %s" % exc) from exc
        return resolved

    def _ensure_render_target(self, size: int):
        if self.render_target is not None and self.render_target_size == size:
            return
        if self.render_target is not None:
            try:
                self.capture_actor.capture_component2d.texture_target = None
                unreal.RenderingLibrary.release_render_target2d(self.render_target)
            except Exception:
                pass
        self.render_target = unreal.RenderingLibrary.create_render_target2d(
            self.world,
            size,
            size,
            unreal.TextureRenderTargetFormat.RTF_RGBA16F,
            unreal.LinearColor(0.0, 0.0, 0.0, 1.0),
            False,
            False,
        )
        if self.render_target is None:
            raise CaptureError("The capture image could not be created.")
        self.render_target_size = size

    def _ensure_bounds_render_target(self):
        if self.bounds_render_target is not None:
            return
        self.bounds_render_target = unreal.RenderingLibrary.create_render_target2d(
            self.world,
            VISUAL_BOUNDS_PROBE_SIZE,
            VISUAL_BOUNDS_PROBE_SIZE,
            unreal.TextureRenderTargetFormat.RTF_RGBA16F,
            unreal.LinearColor(0.0, 0.0, 0.0, 1.0),
            False,
            False,
        )
        if self.bounds_render_target is None:
            raise CaptureError("Automatic framing could not be initialized.")

    def _restore_current_show_only(self, component, render_mode):
        component.primitive_render_mode = render_mode
        component.clear_show_only_components()
        if render_mode == unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST:
            for actor in self.source_actors:
                component.show_only_actor_components(actor, True)

    def _probe_visual_bounds_triplet(
        self,
        center: tuple[float, float, float],
        ortho_width: float,
    ):
        self._ensure_bounds_render_target()
        component = self.capture_actor.capture_component2d
        saved = {
            "texture_target": component.texture_target,
            "capture_source": component.capture_source,
            "projection_type": component.projection_type,
            "ortho_width": float(component.ortho_width),
            "fov_angle": float(component.fov_angle),
            "primitive_render_mode": component.primitive_render_mode,
            "camera_cut_this_frame": bool(component.camera_cut_this_frame),
            "location": self.capture_actor.get_actor_location(),
            "rotation": self.capture_actor.get_actor_rotation(),
        }
        projection_samples = []
        edge_touched = False
        captures = 0
        probe_center = unreal.Vector(*center)
        probe_distance = max(
            100.0,
            float(ortho_width) * 2.0,
            float(self.coarse_bounds_radius) * 2.0,
        )
        try:
            component.texture_target = self.bounds_render_target
            component.capture_source = unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR
            component.projection_type = unreal.CameraProjectionMode.ORTHOGRAPHIC
            component.ortho_width = float(ortho_width)
            component.primitive_render_mode = (
                unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST
            )
            component.clear_show_only_components()
            for actor in self.source_actors:
                component.show_only_actor_components(actor, True)
            for axis in (
                unreal.Vector(1.0, 0.0, 0.0),
                unreal.Vector(0.0, 1.0, 0.0),
                unreal.Vector(0.0, 0.0, 1.0),
            ):
                location = probe_center + axis * probe_distance
                rotation = unreal.MathLibrary.find_look_at_rotation(
                    location, probe_center
                )
                self.capture_actor.set_actor_location(location, False, False)
                self.capture_actor.set_actor_rotation(rotation, False)
                component.camera_cut_this_frame = True
                component.capture_scene()
                captures += 1
                pixels = unreal.RenderingLibrary.read_render_target_raw(
                    self.world, self.bounds_render_target, False
                )
                pixel_bounds = probe_visible_alpha_bounds(
                    pixels, VISUAL_BOUNDS_PROBE_SIZE
                )
                if pixel_bounds is None:
                    continue
                edge_touched = edge_touched or pixel_bounds_touch_edge(
                    pixel_bounds, VISUAL_BOUNDS_PROBE_SIZE
                )
                right = unreal.MathLibrary.get_right_vector(rotation)
                up = unreal.MathLibrary.get_up_vector(rotation)
                forward = unreal.MathLibrary.get_forward_vector(rotation)
                projection_samples.append(
                    projection_intervals(
                        pixel_bounds,
                        VISUAL_BOUNDS_PROBE_SIZE,
                        ortho_width,
                        center,
                        self._vector_tuple(right),
                        self._vector_tuple(up),
                        self._vector_tuple(forward),
                    )
                )
            return (
                combine_projection_intervals(projection_samples),
                edge_touched,
                captures,
                len(projection_samples),
            )
        finally:
            component.texture_target = saved["texture_target"]
            component.capture_source = saved["capture_source"]
            component.projection_type = saved["projection_type"]
            component.ortho_width = saved["ortho_width"]
            component.fov_angle = saved["fov_angle"]
            component.camera_cut_this_frame = saved["camera_cut_this_frame"]
            self.capture_actor.set_actor_location(saved["location"], False, False)
            self.capture_actor.set_actor_rotation(saved["rotation"], False)
            self._restore_current_show_only(
                component, saved["primitive_render_mode"]
            )

    def _use_coarse_bounds(self, reason: str, probe_count: int):
        self.bounds_center = self.coarse_bounds_center
        self.bounds_extent = self.coarse_bounds_extent
        self.bounds_radius = self.coarse_bounds_radius
        self._visual_bounds_method = "actor_fallback"
        self._visual_bounds_fallback = str(reason)
        self._visual_bounds_probe_count = int(probe_count)
        self._visual_bounds_dirty = False
        unreal.log_warning(
            "[ThumbnailCreator] Visual bounds fallback (%s)." % reason
        )

    def _ensure_visual_bounds(self):
        if not self._visual_bounds_dirty or not self.source_actors:
            return
        coarse = bounds_from_center_extent(
            self._vector_tuple(self.coarse_bounds_center),
            self._vector_tuple(self.coarse_bounds_extent),
        )
        center = coarse.center
        ortho_width = max(1.0, coarse.maximum_span * 1.10)
        candidate = None
        edge_touched = False
        probe_count = 0
        valid_views = 0
        final_width = ortho_width
        try:
            for pass_index in range(VISUAL_BOUNDS_MAX_PASSES):
                candidate, edge_touched, captures, valid_views = (
                    self._probe_visual_bounds_triplet(center, ortho_width)
                )
                probe_count += captures
                final_width = ortho_width
                if candidate is None:
                    self._use_coarse_bounds(
                        "insufficient_silhouette_views_%d" % valid_views,
                        probe_count,
                    )
                    return
                next_frame = next_probe_frame(
                    center,
                    ortho_width,
                    candidate,
                    edge_touched=edge_touched,
                    pass_index=pass_index,
                    maximum_passes=VISUAL_BOUNDS_MAX_PASSES,
                )
                if next_frame is None:
                    break
                center, ortho_width = next_frame
            if candidate is None:
                self._use_coarse_bounds("no_silhouette", probe_count)
                return
            if edge_touched:
                self._use_coarse_bounds(
                    "silhouette_touches_probe_edge", probe_count
                )
                return
            visual = inflate_bounds(
                candidate,
                absolute_padding=final_width / VISUAL_BOUNDS_PROBE_SIZE,
                fractional_padding=0.02,
            )
            if visual.radius <= 1.0e-4 or not math.isfinite(visual.radius):
                self._use_coarse_bounds("invalid_visual_bounds", probe_count)
                return
            self.bounds_center = unreal.Vector(*visual.center)
            self.bounds_extent = unreal.Vector(*visual.extent)
            self.bounds_radius = visual.radius
            self._visual_bounds_method = "orthographic_alpha"
            self._visual_bounds_fallback = ""
            self._visual_bounds_probe_count = probe_count
            self._visual_bounds_dirty = False
            unreal.log(
                "[ThumbnailCreator] Visual bounds resolved in %d probes "
                "(radius %.3f -> %.3f)."
                % (probe_count, self.coarse_bounds_radius, self.bounds_radius)
            )
        except Exception as exc:
            self._use_coarse_bounds("probe_error: %s" % exc, probe_count)

    def _camera_transform(self, camera: CameraState, source_kind: SourceKind):
        if self.viewport_override and source_kind == SourceKind.WHOLE_VIEW:
            return self.viewport_override
        yaw = math.radians(float(camera.yaw))
        pitch = math.radians(float(camera.pitch))
        direction = unreal.Vector(
            math.cos(pitch) * math.cos(yaw),
            math.cos(pitch) * math.sin(yaw),
            math.sin(pitch),
        )
        half_fov = math.radians(float(camera.fov) * 0.5)
        distance = self.bounds_radius / max(0.02, math.sin(half_fov))
        distance *= float(camera.framing_margin) * max(0.02, float(camera.dolly))
        rough_location = self.bounds_center + direction * distance
        rough_rotation = unreal.MathLibrary.find_look_at_rotation(rough_location, self.bounds_center)
        right = unreal.MathLibrary.get_right_vector(rough_rotation)
        up = unreal.MathLibrary.get_up_vector(rough_rotation)
        target = self.bounds_center + right * (float(camera.pan_x) * self.bounds_radius) + up * (float(camera.pan_y) * self.bounds_radius)
        location = target + direction * distance
        rotation = unreal.MathLibrary.find_look_at_rotation(location, target)
        rotation.roll = float(camera.roll)
        return location, rotation, float(camera.fov)

    def _bounds_metadata(self):
        if self._visual_bounds_method == "viewport":
            return {
                "bounds_method": "viewport",
                "coarse_bounds": None,
                "visual_bounds": None,
                "visual_bounds_probe_count": 0,
                "visual_bounds_fallback": "",
            }

        def payload(center, extent, radius):
            return {
                "center": list(self._vector_tuple(center)),
                "extent": list(self._vector_tuple(extent)),
                "radius": float(radius),
            }

        return {
            "bounds_method": self._visual_bounds_method,
            "coarse_bounds": payload(
                self.coarse_bounds_center,
                self.coarse_bounds_extent,
                self.coarse_bounds_radius,
            ),
            "visual_bounds": payload(
                self.bounds_center,
                self.bounds_extent,
                self.bounds_radius,
            ),
            "visual_bounds_probe_count": self._visual_bounds_probe_count,
            "visual_bounds_fallback": self._visual_bounds_fallback,
        }

    def read_viewport_camera(self):
        if self.level_sub is None:
            return None
        try:
            key = self.level_sub.get_active_viewport_config_key()
            location, rotation = self.level_sub.get_level_viewport_camera_info(key)
            fov = float(self.level_sub.get_level_viewport_fov(key))
            self.viewport_override = (location, rotation, fov)
            return location, rotation, fov
        except Exception:
            return None

    def capture(self, request: CaptureRequest, png_path: str | None = None) -> CapturedFrame:
        # Pillow is deliberately imported only for the authoritative CPU export
        # path. The Slate GPU preview uses the lightweight bounds helper above.

        started = time.perf_counter()
        self.prepare_source(request.source)
        output_size = int(request.export.output_size)
        supersample = int(request.export.supersample)
        capture_size = output_size * supersample
        if output_size < 32 or supersample < 1 or capture_size > MAX_CAPTURE_SIZE:
            raise CaptureError("Invalid output/supersampling size (%d px render)." % capture_size)
        if not 5.0 <= float(request.camera.fov) <= 120.0:
            raise CaptureError(
                "Field of view must be between 5 and 120 degrees."
            )

        studio = (
            request.source.kind != SourceKind.WHOLE_VIEW
            and request.lighting.mode == LightingMode.STUDIO
        )
        context = (
            isolate_lighting_channels(self._source_primitive_components())
            if studio
            else nullcontext(0)
        )
        try:
            with context:
                return self._capture_prepared(
                    request,
                    png_path,
                    started,
                    output_size,
                    supersample,
                    capture_size,
                    studio,
                )
        except CaptureError:
            raise
        except Exception as exc:
            if studio:
                raise CaptureError("Studio capture failed: %s" % exc) from exc
            raise

    def _capture_prepared(
        self,
        request,
        png_path,
        started,
        output_size,
        supersample,
        capture_size,
        studio,
    ):
        self._ensure_capture_actor()
        try:
            self._set_capture_show_flags(studio)
        except Exception as exc:
            if studio:
                raise CaptureError(
                    "Studio show-flag configuration failed: %s" % exc
                ) from exc
            raise
        try:
            self._configure_capture_exposure(studio)
        except Exception as exc:
            if studio:
                raise CaptureError(
                    "Studio exposure configuration failed: %s" % exc
                ) from exc
            raise
        if request.source.kind != SourceKind.WHOLE_VIEW:
            self._ensure_visual_bounds()
        self._ensure_render_target(capture_size)
        component = self.capture_actor.capture_component2d
        component.texture_target = self.render_target
        component.capture_source = unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR
        component.fov_angle = float(request.camera.fov)
        if request.source.kind == SourceKind.WHOLE_VIEW:
            self.read_viewport_camera()
            component.primitive_render_mode = unreal.SceneCapturePrimitiveRenderMode.PRM_RENDER_SCENE_PRIMITIVES
            component.clear_show_only_components()
        else:
            component.primitive_render_mode = unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST
            component.clear_show_only_components()
            for actor in self.source_actors:
                component.show_only_actor_components(actor, True)

        location, rotation, fov = self._camera_transform(request.camera, request.source.kind)
        self.capture_actor.set_actor_location(location, False, False)
        self.capture_actor.set_actor_rotation(rotation, False)
        component.fov_angle = float(fov)
        fast_preview = bool(
            request.preview and request.preview_fast and supersample == 1
        )
        resolved_studio_lights = ()
        studio_highlight_cache_key = None
        studio_highlight_scale = 1.0
        studio_highlight_cache_hit = False
        studio_highlight_metadata = None
        if studio:
            studio_highlight_cache_key = self._studio_highlight_cache_key(
                request.lighting, request.camera
            )
            studio_highlight_cache_hit = (
                studio_highlight_cache_key in self._studio_highlight_scale_cache
            )
            studio_highlight_scale = self._studio_highlight_scale_cache.get(
                studio_highlight_cache_key, 1.0
            )
        try:
            if studio:
                resolved_studio_lights = self._configure_studio_lights(
                    request.lighting,
                    rotation,
                    intensity_scale=studio_highlight_scale,
                )
                self._set_studio_lights_visible(True)
            component.camera_cut_this_frame = True
            component.capture_scene()
            # Studio uses a second tone-curved color pass. SceneColor remains
            # the alpha source; fast previews read both buffers as 8-bit colors,
            # while refined previews and exports retain the raw HDR path.
            if fast_preview:
                pixels = unreal.RenderingLibrary.read_render_target(self.world, self.render_target, False)
                bounds = color_visible_alpha_bounds(
                    pixels, capture_size
                )
            else:
                pixels = unreal.RenderingLibrary.read_render_target_raw(self.world, self.render_target, False)
                bounds = visible_alpha_bounds(pixels, capture_size)
            if request.source.kind != SourceKind.WHOLE_VIEW and bounds is None:
                raise CaptureError(
                    "The selected source is not visible. "
                    "Check its materials and framing."
                )

            auto_fitted = False
            if (
                request.source.kind != SourceKind.WHOLE_VIEW
                and request.camera.auto_fit
                and bounds
                and (
                    self._source_changed
                    or not alpha_bounds_in_frame(
                        bounds, capture_size
                    )
                )
            ):
                min_x, min_y, max_x, max_y = bounds
                span = max(max_x - min_x + 1, max_y - min_y + 1)
                target_span = capture_size / max(1.0, float(request.camera.framing_margin))
                scale = max(0.02, min(20.0, span / target_span))
                image_center = (capture_size - 1) * 0.5
                normalized_x = (((min_x + max_x) * 0.5) - image_center) / (capture_size * 0.5)
                normalized_y = (((min_y + max_y) * 0.5) - image_center) / (capture_size * 0.5)
                delta = location - self.bounds_center
                current_distance = math.sqrt(float(delta.x) ** 2 + float(delta.y) ** 2 + float(delta.z) ** 2)
                view_half_width = current_distance * math.tan(math.radians(float(fov) * 0.5))
                request.camera.pan_x += normalized_x * view_half_width / self.bounds_radius
                request.camera.pan_y -= normalized_y * view_half_width / self.bounds_radius
                request.camera.dolly = max(0.02, min(20.0, request.camera.dolly * scale))
                location, rotation, fov = self._camera_transform(request.camera, request.source.kind)
                self.capture_actor.set_actor_location(location, False, False)
                self.capture_actor.set_actor_rotation(rotation, False)
                if studio:
                    studio_highlight_cache_key = self._studio_highlight_cache_key(
                        request.lighting, request.camera
                    )
                    studio_highlight_cache_hit = (
                        studio_highlight_cache_key
                        in self._studio_highlight_scale_cache
                    )
                    studio_highlight_scale = (
                        self._studio_highlight_scale_cache.get(
                            studio_highlight_cache_key, 1.0
                        )
                    )
                    resolved_studio_lights = self._configure_studio_lights(
                        request.lighting,
                        rotation,
                        intensity_scale=studio_highlight_scale,
                    )
                component.camera_cut_this_frame = True
                component.capture_scene()
                if fast_preview:
                    pixels = unreal.RenderingLibrary.read_render_target(self.world, self.render_target, False)
                    bounds = color_visible_alpha_bounds(
                        pixels, capture_size
                    )
                else:
                    pixels = unreal.RenderingLibrary.read_render_target_raw(self.world, self.render_target, False)
                    bounds = visible_alpha_bounds(
                        pixels, capture_size
                    )
                auto_fitted = True

            color_pixels = None
            if studio:
                try:
                    component.capture_source = (
                        unreal.SceneCaptureSource.SCS_FINAL_TONE_CURVE_HDR
                    )
                    read_color = (
                        unreal.RenderingLibrary.read_render_target
                        if fast_preview
                        else unreal.RenderingLibrary.read_render_target_raw
                    )

                    def capture_tone_color():
                        component.camera_cut_this_frame = True
                        component.capture_scene()
                        return read_color(self.world, self.render_target, False)

                    color_pixels = capture_tone_color()
                    initial_highlights = sampled_luminance_stats(
                        pixels,
                        color_pixels,
                        raw=not fast_preview,
                    )
                    final_highlights = initial_highlights
                    pending = False
                    calibrated = False
                    if studio_highlights_need_protection(initial_highlights):
                        if fast_preview:
                            pending = True
                        elif (
                            not fast_preview
                            and studio_highlight_scale
                            > STUDIO_HIGHLIGHT_MIN_SCALE + 1.0e-4
                        ):
                            low_scale = STUDIO_HIGHLIGHT_MIN_SCALE
                            low_lights = self._configure_studio_lights(
                                request.lighting,
                                rotation,
                                intensity_scale=low_scale,
                            )
                            low_color_pixels = capture_tone_color()
                            low_highlights = sampled_luminance_stats(
                                pixels,
                                low_color_pixels,
                                raw=True,
                            )
                            chosen_scale = interpolate_studio_highlight_scale(
                                studio_highlight_scale,
                                initial_highlights["p95"],
                                low_scale,
                                low_highlights["p95"],
                            )
                            if abs(chosen_scale - low_scale) <= 0.01:
                                studio_highlight_scale = low_scale
                                resolved_studio_lights = low_lights
                                color_pixels = low_color_pixels
                                final_highlights = low_highlights
                            else:
                                studio_highlight_scale = chosen_scale
                                resolved_studio_lights = (
                                    self._configure_studio_lights(
                                        request.lighting,
                                        rotation,
                                        intensity_scale=chosen_scale,
                                    )
                                )
                                color_pixels = capture_tone_color()
                                final_highlights = sampled_luminance_stats(
                                    pixels,
                                    color_pixels,
                                    raw=True,
                                )
                                if (
                                    studio_highlights_need_protection(
                                        final_highlights
                                    )
                                    and not studio_highlights_need_protection(
                                        low_highlights
                                    )
                                ):
                                    studio_highlight_scale = low_scale
                                    resolved_studio_lights = low_lights
                                    color_pixels = low_color_pixels
                                    final_highlights = low_highlights
                            calibrated = True
                        elif not fast_preview:
                            calibrated = True
                    if studio_highlight_cache_key is not None and not pending:
                        self._remember_studio_highlight_scale(
                            studio_highlight_cache_key,
                            studio_highlight_scale,
                        )
                    studio_highlight_metadata = {
                        "scale": round(float(studio_highlight_scale), 6),
                        "cache_hit": bool(studio_highlight_cache_hit),
                        "calibrated": bool(calibrated),
                        "pending_refined": bool(pending),
                        "initial": initial_highlights,
                        "final": final_highlights,
                    }
                except Exception as exc:
                    raise CaptureError(
                        "Studio tone-curved color capture failed: %s" % exc
                    ) from exc
                finally:
                    component.capture_source = (
                        unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR
                    )

            if fast_preview:
                image, stats = colors_to_preview_image(
                    pixels,
                    output_size,
                    color_pixels=color_pixels,
                )
            else:
                image, stats = pixels_to_image(
                    pixels,
                    output_size,
                    supersample,
                    color_pixels=color_pixels,
                )
            image = apply_adjustments(image, request.adjust)
            if png_path:
                save_png(
                    image,
                    png_path,
                    transparent=request.export.transparent,
                    background_color=request.export.background_color,
                )
            self._source_changed = False
            effective_lighting = request.lighting.to_dict()
            effective_lighting["mode"] = (
                LightingMode.STUDIO.value if studio else LightingMode.WORLD.value
            )
            effective_lighting["effective_preset"] = (
                request.lighting.preset if studio else LightingMode.WORLD.value
            )
            if studio:
                effective_lighting["capture_exposure_bias"] = (
                    STUDIO_EXPOSURE_BIAS
                )
                effective_lighting["highlight_scale"] = round(
                    float(studio_highlight_scale), 6
                )
            result = CaptureResult(
                success=True,
                source_key=request.source.key,
                png_path=os.path.abspath(png_path) if png_path else "",
                output_size=output_size,
                capture_size=capture_size,
                elapsed_seconds=time.perf_counter() - started,
                alpha_nonzero=stats["alpha_nonzero"],
                alpha_fractional=stats["alpha_fractional"],
                metadata={
                    **self._bounds_metadata(),
                    "alpha_bounds": bounds,
                    "camera_location": str(location),
                    "camera_rotation": str(rotation),
                    "auto_fitted": auto_fitted,
                    "lighting": effective_lighting,
                    "studio_highlights": studio_highlight_metadata,
                    "resolved_studio_lights": [
                        {
                            "role": spec.role,
                            "position_relative": list(spec.position),
                            "intensity_lumens": spec.intensity,
                            "temperature_kelvin": spec.temperature_kelvin,
                            "size": spec.size,
                            "cast_shadows": spec.cast_shadows,
                        }
                        for spec in resolved_studio_lights
                    ],
                    "runtime_contract": "thumbnail_creator_lighting_v3",
                    "fitted_camera": {
                        "yaw": request.camera.yaw,
                        "pitch": request.camera.pitch,
                        "roll": request.camera.roll,
                        "pan_x": request.camera.pan_x,
                        "pan_y": request.camera.pan_y,
                        "dolly": request.camera.dolly,
                        "fov": request.camera.fov,
                        "framing_margin": request.camera.framing_margin,
                        "auto_fit": request.camera.auto_fit,
                    },
                },
            )
            return CapturedFrame(image=image, result=result)
        finally:
            if studio:
                self._set_studio_lights_visible(False)

    def frame_source(self, camera: CameraState):
        camera.pan_x = 0.0
        camera.pan_y = 0.0
        camera.dolly = 1.0
        camera.auto_fit = True
        self._invalidate_visual_bounds("frame_requested")
        self._source_changed = True

    def cleanup(self):
        if self.closed:
            return
        if self.capture_actor is not None:
            try:
                self.capture_actor.capture_component2d.texture_target = None
            except Exception:
                pass
        if self.render_target is not None:
            try:
                unreal.RenderingLibrary.release_render_target2d(self.render_target)
            except Exception:
                pass
        self.render_target = None
        if self.bounds_render_target is not None:
            try:
                unreal.RenderingLibrary.release_render_target2d(
                    self.bounds_render_target
                )
            except Exception:
                pass
        self.bounds_render_target = None
        self._destroy_studio_lights()
        self._destroy_owned_sources()
        if self.capture_actor is not None:
            try:
                self.actor_sub.destroy_actor(self.capture_actor)
            except Exception as exc:
                unreal.log_warning("[ThumbnailCreator] Capture cleanup failed: %s" % exc)
        self.capture_actor = None
        try:
            valid = [actor for actor in self.previous_selection if actor is not None]
            self.actor_sub.set_selected_level_actors(valid)
        except Exception as exc:
            unreal.log_warning("[ThumbnailCreator] Could not restore actor selection: %s" % exc)
        self.closed = True


# ============================================================================
# Gpu Preview
# ============================================================================

PREVIEW_SIZE = 384
FPS_SAMPLE_WINDOW = 90
COLOR_PARAMETER = "ColorTexture"
ALPHA_PARAMETER = "AlphaTexture"
OUTER_MASK_PARAMETER = "OuterSilhouetteTexture"
CAPTURE_COLOR_LABEL = "__ThumbnailCreator_GPU_Color"
CAPTURE_ALPHA_LABEL = "__ThumbnailCreator_GPU_Alpha"
TRANSIENT_PACKAGE_PATH = "/Engine/Transient"


_MATERIAL_CODE = r"""
// SlateIM stretches SImage to its resizable slot and this UEFN build does not
// expose SScaleBox. Recover the widget aspect from UV screen derivatives, then
// map the largest centered 1:1 region back to the square RenderTarget.
float2 widgetUV = saturate(UV);
float2 uvDx = ddx(UV);
float2 uvDy = ddy(UV);
float widgetAspect = max(length(uvDy), 1.0e-6) / max(length(uvDx), 1.0e-6);
float2 squareUV = widgetUV;
if (widgetAspect > 1.0)
{
    squareUV.x = (widgetUV.x - 0.5) * widgetAspect + 0.5;
}
else
{
    squareUV.y = (widgetUV.y - 0.5) / max(widgetAspect, 1.0e-6) + 0.5;
}
float squareMask =
    step(0.0, squareUV.x) * step(squareUV.x, 1.0) *
    step(0.0, squareUV.y) * step(squareUV.y, 1.0);
float2 uv = saturate(squareUV);
float4 sampledColor = ColorTexture.Sample(ColorTextureSampler, uv);
float sourceAlpha = saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv).a);
float useOuterSilhouette = saturate(UseOuterSilhouette);
float outlineSourceAlpha = lerp(
    sourceAlpha,
    saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv).r),
    useOuterSilhouette
);

float3 rgb = max(sampledColor.rgb, 0.0);
float3 originalRgb = saturate(rgb);
float4 K = float4(0.0, -0.3333333333, 0.6666666667, -1.0);
float4 p = lerp(float4(rgb.bg, K.wz), float4(rgb.gb, K.xy), step(rgb.b, rgb.g));
float4 q = lerp(float4(p.xyw, rgb.r), float4(rgb.r, p.yzx), step(p.x, rgb.r));
float d = q.x - min(q.w, q.y);
float e = 1.0e-10;
float3 hsv = float3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
hsv.x = frac(hsv.x + HueDegrees / 360.0);
float3 hueRgb = saturate(abs(frac(hsv.xxx + float3(0.0, 0.6666666667, 0.3333333333)) * 6.0 - 3.0) - 1.0);
rgb = hsv.z * lerp(float3(1.0, 1.0, 1.0), hueRgb, hsv.y);

float luminance = dot(rgb, float3(0.299, 0.587, 0.114));
rgb = lerp(luminance.xxx, rgb, max(Saturation, 0.0));
rgb *= max(Brightness, 0.0) * exp2(Exposure);
rgb = (rgb - 0.5) * max(Contrast, 0.0) + 0.5;
rgb = saturate(rgb);
rgb = lerp(rgb, originalRgb, saturate(ComparisonBypass));

float expandedAlpha = outlineSourceAlpha;
float2 radius = TexelSize.xy * max(OutlineWidth, 0.0);
float2 halfRadius = radius * 0.5;
float sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2( radius.x, 0.0)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2( radius.x, 0.0)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(-radius.x, 0.0)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(-radius.x, 0.0)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(0.0,  radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(0.0,  radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(0.0, -radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(0.0, -radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2( radius.x,  radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2( radius.x,  radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(-radius.x,  radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(-radius.x,  radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2( radius.x, -radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2( radius.x, -radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(-radius.x, -radius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(-radius.x, -radius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2( halfRadius.x,  halfRadius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2( halfRadius.x,  halfRadius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(-halfRadius.x,  halfRadius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(-halfRadius.x,  halfRadius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2( halfRadius.x, -halfRadius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2( halfRadius.x, -halfRadius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);
sampledOutlineAlpha = lerp(saturate(1.0 - AlphaTexture.Sample(AlphaTextureSampler, uv + float2(-halfRadius.x, -halfRadius.y)).a), saturate(OuterSilhouetteTexture.Sample(OuterSilhouetteTextureSampler, uv + float2(-halfRadius.x, -halfRadius.y)).r), useOuterSilhouette);
expandedAlpha = max(expandedAlpha, sampledOutlineAlpha);

float ringAlpha = saturate(expandedAlpha - outlineSourceAlpha) * saturate(OutlineAlpha) * (1.0 - saturate(ComparisonBypass));
float foregroundAlpha = sourceAlpha + ringAlpha * (1.0 - sourceAlpha);
float3 foregroundPremultiplied = rgb * sourceAlpha + OutlineColor.rgb * ringAlpha * (1.0 - sourceAlpha);
float3 foreground = foregroundPremultiplied / max(foregroundAlpha, 1.0e-5);

float checker = fmod(floor(uv.x * 24.0) + floor(uv.y * 24.0), 2.0);
float3 checkerColor = lerp(float3(0.14, 0.15, 0.17), float3(0.24, 0.25, 0.27), checker);
float3 background = checkerColor;
background = lerp(background, float3(0.055, 0.065, 0.075), step(0.5, BackgroundMode) * (1.0 - step(1.5, BackgroundMode)));
background = lerp(background, float3(0.78, 0.78, 0.78), step(1.5, BackgroundMode) * (1.0 - step(2.5, BackgroundMode)));
background = lerp(background, SolidColor.rgb, step(2.5, BackgroundMode));

float3 finalColor = lerp(background, foreground, foregroundAlpha);

float lineWidth = max(TexelSize.x, TexelSize.y) * 1.35;
float thirdsDistance = min(
    min(abs(uv.x - 0.3333333333), abs(uv.x - 0.6666666667)),
    min(abs(uv.y - 0.3333333333), abs(uv.y - 0.6666666667))
);
float thirdsLine = (1.0 - step(lineWidth, thirdsDistance)) * saturate(OverlayThirds);
float centerDistance = min(abs(uv.x - 0.5), abs(uv.y - 0.5));
float centerExtent = step(abs(uv.x - 0.5), 0.045) + step(abs(uv.y - 0.5), 0.045);
float centerLine = (1.0 - step(lineWidth, centerDistance)) * saturate(centerExtent) * saturate(OverlayCenter);
float safeHalfExtent = saturate(SafeFramePercent * 0.005);
float2 safeDistance = abs(abs(uv - 0.5) - safeHalfExtent.xx);
float insideSafeBand = step(abs(uv.x - 0.5), safeHalfExtent) * step(abs(uv.y - 0.5), safeHalfExtent);
float safeLine = (1.0 - step(lineWidth, min(safeDistance.x, safeDistance.y))) * insideSafeBand * step(1.0, SafeFramePercent);
float overlayMask = saturate(max(max(thirdsLine, centerLine), safeLine));
float3 overlayColor = lerp(float3(0.92, 0.95, 1.0), float3(0.0, 0.0, 0.0), step(0.45, dot(finalColor, float3(0.299, 0.587, 0.114))));
finalColor = lerp(finalColor, overlayColor, overlayMask * 0.72);
// Paint outside the square because the transient UI material must remain
// opaque for reliable SlateIM rendering.
finalColor = lerp(float3(0.018, 0.021, 0.026), finalColor, squareMask);
return float4(saturate(finalColor), 1.0);
"""


def _new_key(name: str):
    key = unreal.Key()
    key.set_editor_property("key_name", name)
    return key


def _linear_color(rgba) -> unreal.LinearColor:
    values = list(rgba or (0, 0, 0, 255))
    while len(values) < 4:
        values.append(255)
    return unreal.LinearColor(
        max(0.0, min(1.0, float(values[0]) / 255.0)),
        max(0.0, min(1.0, float(values[1]) / 255.0)),
        max(0.0, min(1.0, float(values[2]) / 255.0)),
        max(0.0, min(1.0, float(values[3]) / 255.0)),
    )


class GpuPreviewSession:
    """Own captures, RenderTargets, a transient mask, and a UI material."""

    def __init__(self, capture_session):
        self.capture_session = capture_session
        self.world = capture_session.world
        self.actor_subsystem = capture_session.actor_sub
        self.scheduler = PreviewScheduler()
        self.color_actor = None
        self.alpha_actor = None
        self.color_target = None
        self.alpha_target = None
        self.outer_mask_texture = None
        self.outer_mask_ready = False
        self.outer_mask_refreshes = 0
        self.outer_mask_last_ms = 0.0
        self.outer_mask_last_error = ""
        self.base_material = None
        self.material_instance = None
        self.closed = False
        self.capture_frames = 0
        self.capture_times: list[float] = []
        self.last_capture_ms = 0.0
        self.last_error = ""
        self.last_source_key = ""
        self.last_request_signature = ""
        try:
            self._ensure_resources()
        except Exception:
            # Construction can fail while an editor build rejects a material
            # node or capture property. Do not strand partially-created actors.
            self.cleanup()
            raise

    @staticmethod
    def required_slate_api() -> tuple[str, ...]:
        return (
            "begin_window_root",
            "end_root",
            "image_material",
            "can_update_slate_im",
            "is_hovered",
            "is_key_held",
            "get_key_analog_value",
        )

    @classmethod
    def validate_api(cls) -> None:
        slate = getattr(unreal, "SlateIMBlueprintFunctionLibrary", None)
        missing = [
            name
            for name in cls.required_slate_api()
            if slate is None or not hasattr(slate, name)
        ]
        if missing:
            unreal.log_error(
                "[ThumbnailCreator.Preview] Missing Slate functions: %s"
                % ", ".join(missing)
            )
            raise RuntimeError(
                "The preview is not supported by this UEFN version."
            )

    def _create_target(self, clear_color):
        target = unreal.RenderingLibrary.create_render_target2d(
            self.world,
            PREVIEW_SIZE,
            PREVIEW_SIZE,
            unreal.TextureRenderTargetFormat.RTF_RGBA16F,
            clear_color,
            False,
            False,
        )
        if target is None:
            raise RuntimeError("The preview image could not be created.")
        return target

    def _spawn_capture(self, label: str, target, capture_source):
        actor = self.actor_subsystem.spawn_actor_from_class(
            unreal.SceneCapture2D,
            unreal.Vector(0.0, 0.0, -500000.0),
            unreal.Rotator(),
            True,
        )
        if actor is None:
            raise RuntimeError("The preview camera could not be created.")
        actor.set_actor_label(label, False)
        component = actor.capture_component2d
        component.capture_every_frame = False
        component.capture_on_movement = False
        component.always_persist_rendering_state = True
        component.capture_source = capture_source
        component.texture_target = target
        return actor

    @staticmethod
    def _expression(material, expression_class, x: int, y: int):
        return unreal.MaterialEditingLibrary.create_material_expression(
            material, expression_class, x, y
        )

    @staticmethod
    def _parameter_expression(material, expression_class, name: str, x: int, y: int):
        expression = GpuPreviewSession._expression(
            material, expression_class, x, y
        )
        expression.set_editor_property("parameter_name", name)
        return expression

    def _create_material(self):
        # Unreal's new_object outer defaults to the transient package. Keeping
        # that default is deliberate: these objects must resolve below
        # /Engine/Transient and never become Content Browser assets.
        material = unreal.new_object(
            unreal.Material,
            name="ThumbnailCreator_GPUPreviewMaterialV4",
        )
        material.set_editor_property("material_domain", unreal.MaterialDomain.MD_UI)
        # The material composites transparency against the selected preview
        # background itself. Opaque blend is required for SlateIM to display
        # the transient MID reliably in UEFN.
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)

        expressions = {}
        expressions[COLOR_PARAMETER] = self._parameter_expression(
            material,
            unreal.MaterialExpressionTextureObjectParameter,
            COLOR_PARAMETER,
            -1100,
            -300,
        )
        expressions[ALPHA_PARAMETER] = self._parameter_expression(
            material,
            unreal.MaterialExpressionTextureObjectParameter,
            ALPHA_PARAMETER,
            -1100,
            -180,
        )
        expressions[OUTER_MASK_PARAMETER] = self._parameter_expression(
            material,
            unreal.MaterialExpressionTextureObjectParameter,
            OUTER_MASK_PARAMETER,
            -1100,
            -60,
        )
        expressions[COLOR_PARAMETER].set_editor_property("texture", self.color_target)
        expressions[ALPHA_PARAMETER].set_editor_property("texture", self.alpha_target)
        expressions[OUTER_MASK_PARAMETER].set_editor_property(
            "texture", self.alpha_target
        )
        expressions["UV"] = self._expression(
            material, unreal.MaterialExpressionTextureCoordinate, -1100, 40
        )

        scalar_defaults = {
            "HueDegrees": 0.0,
            "Saturation": 1.0,
            "Brightness": 1.0,
            "Contrast": 1.0,
            "Exposure": 0.0,
            "OutlineWidth": 0.0,
            "OutlineAlpha": 1.0,
            "UseOuterSilhouette": 0.0,
            "BackgroundMode": 0.0,
            "ComparisonBypass": 0.0,
            "OverlayThirds": 0.0,
            "OverlayCenter": 0.0,
            "SafeFramePercent": 0.0,
        }
        y = 80
        for name, default in scalar_defaults.items():
            expression = self._parameter_expression(
                material,
                unreal.MaterialExpressionScalarParameter,
                name,
                -1100,
                y,
            )
            expression.set_editor_property("default_value", float(default))
            expressions[name] = expression
            y += 85

        vector_defaults = {
            "OutlineColor": unreal.LinearColor(0.0, 0.0, 0.0, 1.0),
            "SolidColor": unreal.LinearColor(0.125, 0.125, 0.125, 1.0),
            "TexelSize": unreal.LinearColor(
                1.0 / PREVIEW_SIZE, 1.0 / PREVIEW_SIZE, 0.0, 0.0
            ),
        }
        for name, default in vector_defaults.items():
            expression = self._parameter_expression(
                material,
                unreal.MaterialExpressionVectorParameter,
                name,
                -1100,
                y,
            )
            expression.set_editor_property("default_value", default)
            expressions[name] = expression
            y += 100

        custom = self._expression(
            material, unreal.MaterialExpressionCustom, -350, 0
        )
        custom.set_editor_property("description", "Thumbnail Creator GPU composition")
        custom.set_editor_property("code", _MATERIAL_CODE)
        custom.set_editor_property(
            "output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4
        )
        input_names = list(expressions)
        custom_inputs = []
        for input_name in input_names:
            custom_input = unreal.CustomInput()
            custom_input.set_editor_property("input_name", input_name)
            custom_inputs.append(custom_input)
        custom.set_editor_property("inputs", custom_inputs)
        for input_name in input_names:
            if not unreal.MaterialEditingLibrary.connect_material_expressions(
                expressions[input_name], "", custom, input_name
            ):
                raise RuntimeError(
                    "Could not connect preview material input %s." % input_name
                )

        # MP_EmissiveColor accepts the RGB channels from the custom float4.
        # The preview material always paints one of its GPU backgrounds, so its
        # Slate output is intentionally opaque after alpha composition.
        unreal.MaterialEditingLibrary.connect_material_property(
            custom, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR
        )
        opacity = self._expression(
            material, unreal.MaterialExpressionConstant, 100, 100
        )
        opacity.set_editor_property("r", 1.0)
        unreal.MaterialEditingLibrary.connect_material_property(
            opacity, "", unreal.MaterialProperty.MP_OPACITY
        )
        compile_errors = list(
            unreal.MaterialEditingLibrary.recompile_material(material)
        )
        if compile_errors:
            unreal.log_error(
                "[ThumbnailCreator.Preview] Material compilation failed: %s"
                % "; ".join(str(value) for value in compile_errors)
            )
            raise RuntimeError(
                "The preview renderer could not be initialized."
            )

        instance = unreal.new_object(
            unreal.MaterialInstanceDynamic,
            name="ThumbnailCreator_GPUPreviewMIDV4",
        )
        instance.set_editor_property("parent", material)
        instance.set_texture_parameter_value(COLOR_PARAMETER, self.color_target)
        instance.set_texture_parameter_value(ALPHA_PARAMETER, self.alpha_target)
        instance.set_texture_parameter_value(
            OUTER_MASK_PARAMETER, self.alpha_target
        )
        instance.set_vector_parameter_value(
            "TexelSize",
            unreal.LinearColor(1.0 / PREVIEW_SIZE, 1.0 / PREVIEW_SIZE, 0.0, 0.0),
        )
        return material, instance

    def _ensure_resources(self) -> None:
        if self.closed:
            raise RuntimeError("The preview is closed.")
        self.validate_api()
        if self.color_target is None:
            self.color_target = self._create_target(
                unreal.LinearColor(0.01, 0.01, 0.01, 1.0)
            )
        if self.alpha_target is None:
            self.alpha_target = self._create_target(
                unreal.LinearColor(0.0, 0.0, 0.0, 1.0)
            )
        if self.color_actor is None:
            self.color_actor = self._spawn_capture(
                CAPTURE_COLOR_LABEL,
                self.color_target,
                unreal.SceneCaptureSource.SCS_FINAL_TONE_CURVE_HDR,
            )
        if self.alpha_actor is None:
            self.alpha_actor = self._spawn_capture(
                CAPTURE_ALPHA_LABEL,
                self.alpha_target,
                unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR,
            )
        if self.material_instance is None:
            self.base_material, self.material_instance = self._create_material()

    @staticmethod
    def _configure_exposure(component, studio: bool) -> None:
        settings = component.get_editor_property("post_process_settings")
        for override in (
            "override_auto_exposure_method",
            "override_auto_exposure_bias",
            "override_auto_exposure_apply_physical_camera_exposure",
        ):
            settings.set_editor_property(override, bool(studio))
        if studio:
            settings.set_editor_property(
                "auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL
            )
            settings.set_editor_property(
                "auto_exposure_bias", STUDIO_EXPOSURE_BIAS
            )
            settings.set_editor_property(
                "auto_exposure_apply_physical_camera_exposure", False
            )
        component.set_editor_property("post_process_settings", settings)
        component.set_editor_property("post_process_blend_weight", 1.0)

    def _configure_component(self, component, request: CaptureRequest, studio: bool):
        component.show_flag_settings = [
            unreal.EngineShowFlagsSetting(show_flag_name=name, enabled=False)
            for name in disabled_capture_show_flags(studio)
        ]
        self._configure_exposure(component, studio)
        component.clear_show_only_components()
        if request.source.kind == SourceKind.WHOLE_VIEW:
            component.primitive_render_mode = (
                unreal.SceneCapturePrimitiveRenderMode.PRM_RENDER_SCENE_PRIMITIVES
            )
        else:
            component.primitive_render_mode = (
                unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST
            )
            for actor in self.capture_session.source_actors:
                component.show_only_actor_components(actor, True)

    def _invalidate_outer_mask(self) -> None:
        self.outer_mask_ready = False

    def _refresh_outer_mask(self) -> None:
        started = time.perf_counter()
        pixels = unreal.RenderingLibrary.read_render_target(
            self.world,
            self.alpha_target,
            False,
        )
        png_bytes = outer_silhouette_png_from_inverse_alpha_pixels(
            pixels,
            PREVIEW_SIZE,
        )
        texture = unreal.RenderingLibrary.import_buffer_as_texture2d(
            self.world,
            list(png_bytes),
        )
        if texture is None:
            raise RuntimeError(
                "The outer outline preview could not be created."
            )
        try:
            texture.set_editor_property("srgb", False)
            texture.set_editor_property(
                "filter", unreal.TextureFilter.TF_BILINEAR
            )
            texture.set_editor_property(
                "address_x", unreal.TextureAddress.TA_CLAMP
            )
            texture.set_editor_property(
                "address_y", unreal.TextureAddress.TA_CLAMP
            )
        except Exception as exc:
            unreal.log_warning(
                "[ThumbnailCreator.GPUPreview] Transient mask texture settings "
                "could not be fully applied: %s" % exc
            )
        previous_texture = self.outer_mask_texture
        self.material_instance.set_texture_parameter_value(
            OUTER_MASK_PARAMETER,
            texture,
        )
        self.outer_mask_texture = texture
        if previous_texture is not None:
            # ImportBuffer creates a new transient object. Once the MID and
            # this session point at the replacement, queue collection of the
            # now-unreferenced previous mask at the end of the frame.
            previous_texture = None
            unreal.SystemLibrary.collect_garbage()
        self.outer_mask_ready = True
        self.outer_mask_refreshes += 1
        self.outer_mask_last_ms = (
            time.perf_counter() - started
        ) * 1000.0
        self.outer_mask_last_error = ""

    def update_material_parameters(
        self,
        request: CaptureRequest,
        preview_background: str,
        ui_overlays: dict[str, object] | None = None,
    ) -> None:
        instance = self.material_instance
        adjust = request.adjust
        export = request.export
        mode = (
            PREVIEW_BACKGROUNDS.index(preview_background)
            if preview_background in PREVIEW_BACKGROUNDS
            else 0
        )
        if not export.transparent:
            mode = PREVIEW_BACKGROUNDS.index("Solid")
        preview_outline = (
            float(adjust.outline_width)
            * float(PREVIEW_SIZE)
            / max(1.0, float(export.output_size))
        )
        outer_requested = bool(adjust.outer_silhouette_only)
        outer_ready = bool(
            outer_requested
            and self.outer_mask_ready
            and not self.scheduler.live
        )
        outline_alpha = max(
            0.0,
            min(1.0, float(adjust.outline_color[3]) / 255.0),
        )
        if outer_requested and not outer_ready:
            outline_alpha = 0.0
        overlay_values = dict(ui_overlays or {})
        scalar_values = {
            "HueDegrees": adjust.hue,
            "Saturation": adjust.saturation,
            "Brightness": adjust.brightness,
            "Contrast": adjust.contrast,
            "Exposure": adjust.exposure,
            "OutlineWidth": preview_outline,
            "OutlineAlpha": outline_alpha,
            "UseOuterSilhouette": float(outer_ready),
            "BackgroundMode": float(mode),
            "ComparisonBypass": float(
                bool(overlay_values.get("comparison_bypass", False))
            ),
            "OverlayThirds": float(
                bool(overlay_values.get("overlay_thirds", False))
            ),
            "OverlayCenter": float(
                bool(overlay_values.get("overlay_center", False))
            ),
            "SafeFramePercent": float(
                overlay_values.get("safe_frame", 0) or 0
            ),
        }
        for name, value in scalar_values.items():
            instance.set_scalar_parameter_value(name, float(value))
        instance.set_vector_parameter_value(
            "OutlineColor", _linear_color(adjust.outline_color)
        )
        instance.set_vector_parameter_value(
            "SolidColor", _linear_color(export.background_color)
        )

    def mark_dirty(
        self,
        now: float | None = None,
        *,
        silhouette: bool = True,
    ) -> None:
        if self.scheduler.suspended:
            self.scheduler.suspend(False)
        if silhouette:
            self._invalidate_outer_mask()
        self.scheduler.mark_dirty(time.perf_counter() if now is None else now)

    def set_live(self, enabled: bool, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else float(now)
        if enabled and self.scheduler.suspended:
            self.scheduler.suspend(False)
        if bool(enabled) != bool(self.scheduler.live):
            self._invalidate_outer_mask()
        self.scheduler.set_live(enabled, now)
        if not enabled:
            self.scheduler.mark_dirty(now)

    def suspend(self, enabled: bool) -> None:
        self.scheduler.suspend(enabled)

    def _capture(self, request: CaptureRequest) -> None:
        self._ensure_resources()
        session = self.capture_session
        session.prepare_source(request.source)
        if request.source.kind != SourceKind.WHOLE_VIEW:
            session._ensure_capture_actor()
            session._ensure_visual_bounds()
        studio = bool(
            request.source.kind != SourceKind.WHOLE_VIEW
            and request.lighting.mode == LightingMode.STUDIO
        )
        color_component = self.color_actor.capture_component2d
        alpha_component = self.alpha_actor.capture_component2d
        color_component.capture_source = (
            unreal.SceneCaptureSource.SCS_FINAL_TONE_CURVE_HDR
            if studio
            else unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR
        )
        alpha_component.capture_source = unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR
        self._configure_component(color_component, request, studio)
        self._configure_component(alpha_component, request, studio)
        location, rotation, fov = session._camera_transform(
            request.camera, request.source.kind
        )
        for actor in (self.color_actor, self.alpha_actor):
            actor.set_actor_location(location, False, False)
            actor.set_actor_rotation(rotation, False)
            component = actor.capture_component2d
            component.fov_angle = float(fov)
            component.camera_cut_this_frame = True

        context = (
            isolate_lighting_channels(session._source_primitive_components())
            if studio
            else nullcontext(0)
        )
        try:
            with context:
                if studio:
                    session._configure_studio_lights(request.lighting, rotation)
                    session._set_studio_lights_visible(True)
                color_component.capture_scene()
                alpha_component.capture_scene()
        finally:
            if studio:
                session._set_studio_lights_visible(False)

    def tick(
        self,
        request: CaptureRequest | None,
        preview_background: str = "Checker",
        now: float | None = None,
        ui_overlays: dict[str, object] | None = None,
    ) -> bool:
        if self.closed or request is None:
            return False
        now = time.perf_counter() if now is None else float(now)
        self.update_material_parameters(
            request, preview_background, ui_overlays=ui_overlays
        )
        if not self.scheduler.is_due(now):
            return False
        started = time.perf_counter()
        try:
            self._capture(request)
            finished = time.perf_counter()
            self.last_capture_ms = (finished - started) * 1000.0
            self.capture_frames += 1
            self.capture_times.append(now)
            self.capture_times = self.capture_times[-FPS_SAMPLE_WINDOW:]
            self.last_source_key = request.source.key
            self.last_error = ""
            self.scheduler.did_capture(now)
            should_refresh_outer = bool(
                request.adjust.outer_silhouette_only
                and int(request.adjust.outline_width) > 0
                and not self.scheduler.live
                and now >= float(self.scheduler.active_until)
            )
            if should_refresh_outer and not self.outer_mask_ready:
                try:
                    self._refresh_outer_mask()
                except Exception as exc:
                    self.outer_mask_ready = False
                    self.outer_mask_last_error = repr(exc)
                    unreal.log_warning(
                        "[ThumbnailCreator.GPUPreview] Outer silhouette mask "
                        "refresh failed: %s" % exc
                    )
                self.update_material_parameters(
                    request,
                    preview_background,
                    ui_overlays=ui_overlays,
                )
            return True
        except Exception as exc:
            self.last_error = repr(exc)
            self.scheduler.suspend(True)
            unreal.log_error("[ThumbnailCreator.GPUPreview] %s" % exc)
            return False

    def measured_fps(self) -> float:
        if len(self.capture_times) < 2:
            return 0.0
        elapsed = self.capture_times[-1] - self.capture_times[0]
        return (
            (len(self.capture_times) - 1) / elapsed
            if elapsed > 0.0
            else 0.0
        )

    def diagnostics(self) -> dict[str, object]:
        return PreviewDiagnostics(
            target_fps=TARGET_PREVIEW_FPS,
            measured_fps=self.measured_fps(),
            capture_scene_ms=self.last_capture_ms,
            capture_frames=self.capture_frames,
            active=self.scheduler.is_active(time.perf_counter()),
            live=self.scheduler.live,
            suspended=self.scheduler.suspended,
            last_error=self.last_error,
        ).to_dict() | {
            "preview_size": PREVIEW_SIZE,
            "two_capture_path": True,
            "color_target_ready": self.color_target is not None,
            "alpha_target_ready": self.alpha_target is not None,
            "outer_mask_ready": self.outer_mask_ready,
            "outer_mask_texture_ready": self.outer_mask_texture is not None,
            "outer_mask_refreshes": self.outer_mask_refreshes,
            "outer_mask_last_ms": self.outer_mask_last_ms,
            "outer_mask_last_error": self.outer_mask_last_error,
            "material_ready": self.material_instance is not None,
            "material_path": (
                self.base_material.get_path_name()
                if self.base_material is not None
                else ""
            ),
        }

    def cleanup(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.scheduler.suspend(True)
        for actor in (self.color_actor, self.alpha_actor):
            if actor is not None:
                try:
                    component = actor.capture_component2d
                    component.capture_every_frame = False
                    component.capture_on_movement = False
                    component.texture_target = None
                except Exception:
                    pass
        for target in (self.color_target, self.alpha_target):
            if target is not None:
                try:
                    unreal.RenderingLibrary.release_render_target2d(target)
                except Exception as exc:
                    unreal.log_warning(
                        "[ThumbnailCreator.GPUPreview] RenderTarget cleanup failed: %s"
                        % exc
                    )
        for actor in (self.color_actor, self.alpha_actor):
            if actor is not None:
                try:
                    self.actor_subsystem.destroy_actor(actor)
                except Exception as exc:
                    unreal.log_warning(
                        "[ThumbnailCreator.GPUPreview] Capture cleanup failed: %s"
                        % exc
                    )
        if self.material_instance is not None and self.alpha_target is not None:
            try:
                self.material_instance.set_texture_parameter_value(
                    OUTER_MASK_PARAMETER,
                    self.alpha_target,
                )
            except Exception:
                pass
        self.color_actor = None
        self.alpha_actor = None
        self.color_target = None
        self.alpha_target = None
        self.outer_mask_texture = None
        self.outer_mask_ready = False
        self.material_instance = None
        self.base_material = None
        unreal.SystemLibrary.collect_garbage()


# ============================================================================
# Slate App
# ============================================================================

APP_BUILD_TOKEN = (
    os.path.getsize(SCRIPT_PATH),
    os.stat(SCRIPT_PATH).st_mtime_ns,
)

TOOL_NAME = "Thumbnail Creator"
REGISTRY_MODULE_NAME = "_thumbnail_creator_slate_app_v3"
WINDOW_ROOT_ID = "UEFNPythonTools.ThumbnailCreator.SlateV3"
TAB_GROUP_ID = "UEFNPythonTools.ThumbnailCreator.WorkspaceStablePreviewSettingsV3"
WINDOW_TITLE = "Thumbnail Creator"
PREVIEW_TAB_ID = "ThumbnailCreator.Preview"
PREVIEW_SETTINGS_TAB_ID = "ThumbnailCreator.PreviewSettings"
BATCH_TAB_ID = "ThumbnailCreator.Utility.Batch"
OUTLINE_PICKER_SATURATION_STEPS = (
    0.0,
    0.17,
    0.33,
    0.50,
    0.67,
    0.83,
    1.0,
)
OUTLINE_PICKER_VALUE_STEPS = (
    1.0,
    0.85,
    0.68,
    0.50,
    0.32,
    0.14,
    0.0,
)
INSPECTOR_TAB_IDS = {
    name: "ThumbnailCreator.Inspector.%s" % name for name in INSPECTOR_TABS
}

BUILTIN_LIGHTING_PRESETS = (
    ("Neutral", "neutral"),
    ("Soft", "soft"),
    ("Dramatic", "dramatic"),
    ("Flat", "flat"),
)
ROLE_LABELS = ("Key", "Fill", "Rim")
SOURCE_KIND_LABELS = {
    SourceKind.STATIC_MESH: "Static Mesh",
    SourceKind.SKELETAL_MESH: "Skeletal Mesh",
    SourceKind.BLUEPRINT: "Blueprint",
    SourceKind.ACTORS: "Level Actors",
    SourceKind.NIAGARA: "Niagara System",
    SourceKind.WHOLE_VIEW: "Current View",
}
FRAME_RECIPES = {
    "Front": (0.0, 0.0, 0.0),
    "3/4": (35.0, 20.0, 0.0),
    "Top": (0.0, 89.0, 0.0),
    "Tilt": (35.0, 20.0, -12.0),
}


def _registry_module() -> types.ModuleType:
    registry = sys.modules.get(REGISTRY_MODULE_NAME)
    if registry is None:
        registry = types.ModuleType(REGISTRY_MODULE_NAME)
        sys.modules[REGISTRY_MODULE_NAME] = registry
    defaults = {
        "app": None,
        "app_build_token": None,
        "tick_handle": None,
        "tick_callback": None,
        "draw_frame": None,
        "window_open": False,
        "ever_opened": False,
        "reopen_requested": False,
        "cleanup_requested": False,
        "last_error": "",
    }
    for name, value in defaults.items():
        if not hasattr(registry, name):
            setattr(registry, name, value)
    return registry


_REGISTRY = _registry_module()


def format_rgba_hex(values) -> str:
    rgba = list(values or (0, 0, 0, 255))
    while len(rgba) < 4:
        rgba.append(255)
    return "#%02X%02X%02X%02X" % tuple(
        max(0, min(255, int(value))) for value in rgba[:4]
    )


def parse_rgba_hex(value: str):
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        return None
    try:
        return tuple(int(text[index : index + 2], 16) for index in range(0, 8, 2))
    except ValueError:
        return None


def rgba_to_hsva(values):
    """Convert the persisted 8-bit RGBA color to picker-friendly HSVA."""
    rgba = list(values or (0, 0, 0, 255))
    while len(rgba) < 4:
        rgba.append(255)
    red, green, blue, alpha = (
        max(0.0, min(1.0, float(value) / 255.0)) for value in rgba[:4]
    )
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    return (hue * 360.0, saturation, value, alpha)


def hsva_to_rgba(hue, saturation, value, alpha=1.0):
    """Convert the native Slate picker values back to 8-bit RGBA."""
    hue = float(hue) % 360.0
    saturation = max(0.0, min(1.0, float(saturation)))
    value = max(0.0, min(1.0, float(value)))
    alpha = max(0.0, min(1.0, float(alpha)))
    red, green, blue = colorsys.hsv_to_rgb(hue / 360.0, saturation, value)
    return tuple(
        int(round(channel * 255.0))
        for channel in (red, green, blue, alpha)
    )


def _window_params():
    size = unreal.Vector2f()
    size.set_editor_property("x", 1280.0)
    size.set_editor_property("y", 860.0)
    params = unreal.SlateIMWindowParams(
        window_title=WINDOW_TITLE,
        should_reopen=bool(_REGISTRY.reopen_requested),
        always_on_top=False,
    )
    params.set_editor_property("window_size", size)
    return params


def _window_client_width():
    """Return the live Thumbnail Creator client width in Slate/DPI units."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class Rect(ctypes.Structure):
            _fields_ = (
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            )

        user32 = ctypes.windll.user32
        find_window = user32.FindWindowW
        find_window.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        find_window.restype = ctypes.c_void_p
        window = find_window(None, WINDOW_TITLE)
        if not window:
            return None
        rect = Rect()
        if not user32.GetClientRect(window, ctypes.byref(rect)):
            return None
        width = float(max(0, rect.right - rect.left))
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi is not None:
            get_dpi.argtypes = (ctypes.c_void_p,)
            get_dpi.restype = ctypes.c_uint
            dpi = int(get_dpi(window)) or 96
            width *= 96.0 / float(dpi)
        return width
    except Exception:
        return None


def _framing_recipe_column_count(client_width, recipe_count=4):
    """Choose responsive columns for the 28% Camera panel."""
    count = max(1, int(recipe_count))
    if client_width is None or float(client_width) <= 0.0:
        return count
    camera_content_width = max(1.0, float(client_width) * 0.28 - 48.0)
    return max(1, min(count, int(camera_content_width // 62.0)))


class ThumbnailCreatorSlateApp:
    def __init__(self):
        self.store = SlateV3Store()
        self.project_content_root = detect_project_content_root(unreal)
        self.default_import_path = default_import_path(unreal)
        default_export = ExportOptions(
            output_directory=self.store.root,
            import_path=self.default_import_path,
        )
        self.state = ThumbnailCreatorState.from_dict(
            self.store.load_session(), default_export=default_export
        )
        # Sources are chosen explicitly per tool session.  Do not restore the
        # previous asset or adopt the current Content Browser selection on open.
        self.state.source = None
        if not self.state.export.output_directory:
            self.state.export.output_directory = self.store.root
        self._normalize_import_path()
        self.capture_session = CaptureSession()
        self.preview = GpuPreviewSession(self.capture_session)
        self.preview.set_live(self.state.live_preview)
        self.custom_lighting_presets = self.store.load_lighting_presets()

        self.pending_actions: list[tuple[str, object]] = []
        self.button_latches: set[str] = set()
        self.action_in_progress = False
        self.closed = False
        self.source_error = ""
        self.last_viewport_signature = ""
        self.last_viewport_poll = 0.0
        self.drag_mode = ""
        self.last_preview_click = 0.0
        self.tick_times: list[float] = []
        self.combo_signatures: dict[str, tuple[object, ...]] = {}

        import queue

        self.update_settings = load_update_settings()
        self.update_events = queue.Queue()
        self.update_worker_running = False
        self.update_available = ""
        self.update_changelog = ""
        self.update_status = "v%s" % __version__
        self.update_payload = None
        self.update_apply_when_ready = False

        self.batch_rows: list[BatchRow] = []
        self.batch_queue: list[int] = []
        self.batch_mode = ""
        self.batch_cancel = False
        self.batch_started_at = 0.0
        self.batch_completed = 0
        self.batch_ignored: list[str] = []
        self.batch_reserved: set[str] = set()
        self.batch_search = ""
        self.batch_status_index = 0

        self.lighting_role_index = 0
        self.lighting_name_edit = self._lighting_display_name()
        self.background_hex_edit = format_rgba_hex(
            self.state.export.background_color
        )
        self.outline_hex_edit = format_rgba_hex(self.state.adjust.outline_color)
        self.outline_picker_open = False
        self.outline_picker_hsva = rgba_to_hsva(
            self.state.adjust.outline_color
        )
        self.keys = {
            name: _new_key(name)
            for name in (
                "LeftMouseButton",
                "MiddleMouseButton",
                "MouseX",
                "MouseY",
                "MouseWheelAxis",
                "LeftShift",
                "RightShift",
                "LeftControl",
                "RightControl",
                "B",
                "F",
                "Escape",
            )
        }
        self.params = self._create_params()
        self.preview.mark_dirty()
        if (
            self.update_settings.get("check_updates_on_launch")
            and not os.environ.get(_UPDATE_RELOAD_ENV)
        ):
            self._start_update_check()

    @staticmethod
    def _create_params():
        def text_color(red, green, blue, alpha=1.0):
            return unreal.SlateIMTextParams(
                color=unreal.SlateColor(
                    specified_color=unreal.LinearColor(
                        float(red), float(green), float(blue), float(alpha)
                    )
                )
            )

        def tab(title):
            return unreal.SlateIMTabParams(tab_title=title)

        return {
            "text": unreal.SlateIMTextParams(),
            "muted_text": text_color(0.58, 0.62, 0.68),
            "accent_text": text_color(0.22, 0.62, 1.0),
            "success_text": text_color(0.22, 0.78, 0.42),
            "warning_text": text_color(1.0, 0.68, 0.20),
            "error_text": text_color(1.0, 0.30, 0.28),
            "button": unreal.SlateIMButtonParams(enabled=True),
            "edit": unreal.SlateIMEditableTextParams(),
            "combo": unreal.SlateIMComboBoxParams(searchable=False),
            "scroll": unreal.SlateIMScrollBoxParams(),
            "progress": unreal.SlateIMProgressBarParams(),
            "modal": unreal.SlateIMModalDialogParams(title=TOOL_NAME),
            "color_popup": unreal.SlateIMBorderParams(
                orientation=unreal.Orientation.ORIENT_VERTICAL,
                absorb_mouse=True,
                content_padding=unreal.Margin(
                    left=10.0, top=9.0, right=10.0, bottom=10.0
                ),
            ),
            "section": unreal.SlateIMBorderParams(
                orientation=unreal.Orientation.ORIENT_VERTICAL,
                absorb_mouse=False,
                content_padding=unreal.Margin(
                    left=8.0, top=7.0, right=8.0, bottom=8.0
                ),
            ),
            "toolbar": unreal.SlateIMBorderParams(
                orientation=unreal.Orientation.ORIENT_HORIZONTAL,
                absorb_mouse=False,
                content_padding=unreal.Margin(
                    left=8.0, top=5.0, right=8.0, bottom=5.0
                ),
            ),
            "card": unreal.SlateIMBorderParams(
                orientation=unreal.Orientation.ORIENT_VERTICAL,
                absorb_mouse=False,
                content_padding=unreal.Margin(
                    left=6.0, top=6.0, right=6.0, bottom=6.0
                ),
            ),
            "image": unreal.SlateIMImageParams(
                desired_size=unreal.Vector2D(440.0, 440.0)
            ),
            "image128": unreal.SlateIMImageParams(
                desired_size=unreal.Vector2D(128.0, 128.0)
            ),
            "image64": unreal.SlateIMImageParams(
                desired_size=unreal.Vector2D(64.0, 64.0)
            ),
            "swatch_cell": unreal.SlateIMBorderParams(
                orientation=unreal.Orientation.ORIENT_VERTICAL,
                absorb_mouse=False,
                content_padding=unreal.Margin(
                    left=3.0, top=3.0, right=3.0, bottom=3.0
                ),
            ),
            "tabs": {
                "Preview Settings": tab("Preview Settings"),
                "Camera": tab("Camera"),
                "Lighting": tab("Lighting"),
                "Look": tab("Look"),
                "Output": tab("Output"),
                "Preview": tab("Preview"),
                "Batch": tab("Batch"),
            },
        }

    def _log(self, message: str):
        unreal.log("[ThumbnailCreator] %s" % message)

    def _start_update_check(self):
        if self.update_worker_running:
            return
        import threading

        self.update_worker_running = True
        self.update_status = "Checking for updates..."

        def worker():
            try:
                result = check_for_update(include_changelog=True)
                self.update_events.put(("checked", result))
            except Exception as exc:
                self.update_events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _start_update_download(self, *, apply_when_ready: bool):
        if self.update_worker_running or not self.update_available:
            return
        import threading

        version = self.update_available
        self.update_worker_running = True
        self.update_apply_when_ready = bool(apply_when_ready)
        self.update_status = "Downloading and verifying v%s..." % version

        def worker():
            try:
                result = download_update_payload(version)
                self.update_events.put(("downloaded", result))
            except Exception as exc:
                self.update_events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _pump_update_events(self):
        import queue

        try:
            while True:
                kind, value = self.update_events.get_nowait()
                self.update_worker_running = False
                if kind == "checked":
                    if value is None:
                        self.update_status = "Up to date (v%s), or offline." % __version__
                        continue
                    self.update_available = str(value["version"])
                    self.update_changelog = incoming_release_notes(
                        str(value.get("changelog", ""))
                    )
                    self.update_status = "Update v%s is available." % self.update_available
                    if self.update_settings.get("auto_install_updates"):
                        self._start_update_download(apply_when_ready=True)
                elif kind == "downloaded":
                    self.update_payload = value
                    self.update_status = "v%s verified and ready to install." % value["version"]
                    if self.update_apply_when_ready:
                        self._queue("apply_update")
                elif kind == "error":
                    self.update_apply_when_ready = False
                    self.update_status = "Update failed: %s" % value
        except queue.Empty:
            pass

    def _queue(self, action: str, payload=None):
        if len(self.pending_actions) < 32:
            self.pending_actions.append((action, payload))

    def _action_button(
        self,
        label: str,
        action: str,
        payload=None,
        *,
        enabled: bool = True,
        tooltip: str = "",
    ):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        enabled = bool(enabled and not self.action_in_progress)
        if tooltip:
            slate.set_tool_tip(str(tooltip))
        if not enabled:
            slate.begin_disabled_state()
        try:
            pressed = bool(slate.button(label, self.params["button"]))
        finally:
            if not enabled:
                slate.end_disabled_state()
        key = "%s:%s" % (action, repr(payload))
        if pressed:
            if key not in self.button_latches:
                self._queue(action, payload)
            self.button_latches.add(key)
        else:
            self.button_latches.discard(key)

    def _text(self, value, style: str = "text", tooltip: str = ""):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        if tooltip:
            slate.set_tool_tip(str(tooltip))
        unreal.SlateIMBlueprintFunctionLibrary.text(
            str(value), self.params.get("%s_text" % style, self.params["text"])
        )

    def _checkbox(self, label: str, value: bool, tooltip: str = ""):
        if tooltip:
            unreal.SlateIMBlueprintFunctionLibrary.set_tool_tip(str(tooltip))
        result = unreal.SlateIMBlueprintFunctionLibrary.check_box(
            bool(value), unreal.SlateIMCheckBoxParams(label=label)
        )
        if result is None:
            return None
        changed = bool(result)
        return changed if changed != bool(value) else None

    def _combo(
        self,
        values,
        index: int,
        *,
        key: str = "",
        searchable: bool = False,
        tooltip: str = "",
    ):
        values = [str(value) for value in values]
        index = max(0, min(int(index), max(0, len(values) - 1)))
        signature = (tuple(values), index, bool(searchable))
        refresh = bool(key and self.combo_signatures.get(key) != signature)
        params = unreal.SlateIMComboBoxParams(
            searchable=bool(searchable), force_refresh=refresh
        )
        if tooltip:
            unreal.SlateIMBlueprintFunctionLibrary.set_tool_tip(str(tooltip))
        result = unreal.SlateIMBlueprintFunctionLibrary.combo_box(
            values, index, params
        )
        if key:
            final_index = int(result) if result is not None else index
            self.combo_signatures[key] = (
                tuple(values), final_index, bool(searchable)
            )
        if result is None:
            return None
        changed = int(result)
        return changed if changed != index else None

    def _edit(self, value: str, hint: str = "", tooltip: str = ""):
        params = unreal.SlateIMEditableTextParams(hint_text=hint)
        if tooltip:
            unreal.SlateIMBlueprintFunctionLibrary.set_tool_tip(str(tooltip))
        result = unreal.SlateIMBlueprintFunctionLibrary.editable_text(
            str(value), params
        )
        if result is None:
            return None
        changed = str(result)
        return changed if changed != str(value) else None

    def _slider_float(
        self, label, value, minimum, maximum, step, tooltip: str = ""
    ):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_horizontal_stack(False)
        try:
            slate.min_width(88.0)
            slate.max_width(88.0)
            self._text(label, tooltip=tooltip)
            slate.fill()
            slider_result = slate.slider(
                float(value),
                unreal.SlateIMSliderParams(
                    min=float(minimum), max=float(maximum), step=float(step)
                ),
            )
            slate.min_width(76.0)
            slate.max_width(76.0)
            spin_result = slate.spin_box_float(
                float(slider_result) if slider_result is not None else float(value),
                unreal.SlateIMSpinBoxFloatParams(
                    min=float(minimum), max=float(maximum)
                ),
            )
        finally:
            slate.end_horizontal_stack()
        result = spin_result if spin_result is not None else slider_result
        if result is None:
            return None
        changed = float(result)
        return changed if changed != float(value) else None

    @staticmethod
    def _swatch_params(rgba, width=34.0, height=20.0):
        values = list(rgba or (0, 0, 0, 255))
        while len(values) < 4:
            values.append(255)
        # Keep the visual swatch opaque. Alpha is represented explicitly by
        # the A slider/value so a fully transparent color remains clickable.
        color = unreal.LinearColor(
            max(0.0, min(1.0, float(values[0]) / 255.0)),
            max(0.0, min(1.0, float(values[1]) / 255.0)),
            max(0.0, min(1.0, float(values[2]) / 255.0)),
            1.0,
        )
        return unreal.SlateIMImageParams(
            desired_size=unreal.Vector2D(float(width), float(height)),
            color_and_opacity=unreal.SlateColor(specified_color=color),
        )

    def _color_swatch(
        self,
        rgba,
        *,
        width=34.0,
        height=20.0,
        tooltip="",
    ):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        if tooltip:
            slate.set_tool_tip(str(tooltip))
        slate.image_block(self._swatch_params(rgba, width, height))
        return bool(
            slate.is_hovered()
            and slate.is_key_pressed(self.keys["LeftMouseButton"])
        )

    def _sync_outline_picker(self):
        self.outline_picker_hsva = rgba_to_hsva(
            self.state.adjust.outline_color
        )

    def _set_outline_color(
        self,
        rgba,
        *,
        sync_picker=True,
    ):
        color = tuple(
            max(0, min(255, int(round(value)))) for value in tuple(rgba)[:4]
        )
        if len(color) != 4:
            return False
        if sync_picker:
            self.outline_picker_hsva = rgba_to_hsva(color)
        self.outline_hex_edit = format_rgba_hex(color)
        if color == tuple(self.state.adjust.outline_color):
            return False
        self.state.adjust.outline_color = color
        self._dirty(silhouette=False)
        return True

    def _apply_outline_hsva(self):
        hue, saturation, value, alpha = self.outline_picker_hsva
        return self._set_outline_color(
            hsva_to_rgba(hue, saturation, value, alpha),
            sync_picker=False,
        )

    @staticmethod
    def _nearest_outline_picker_step(value: float, steps) -> float:
        return min(steps, key=lambda step: abs(float(step) - float(value)))

    def _draw_outline_swatch_grid(self, hue, saturation, value, alpha):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        selected_saturation = self._nearest_outline_picker_step(
            saturation,
            OUTLINE_PICKER_SATURATION_STEPS,
        )
        selected_value = self._nearest_outline_picker_step(
            value,
            OUTLINE_PICKER_VALUE_STEPS,
        )
        changed = None
        for value_step in OUTLINE_PICKER_VALUE_STEPS:
            slate.begin_horizontal_stack(False)
            try:
                for saturation_step in OUTLINE_PICKER_SATURATION_STEPS:
                    selected = (
                        saturation_step == selected_saturation
                        and value_step == selected_value
                    )
                    slate.min_width(46.0)
                    slate.max_width(46.0)
                    slate.begin_border(
                        "Brushes.Header" if selected else "Brushes.Panel",
                        self.params["swatch_cell"],
                    )
                    try:
                        slate.set_tool_tip(
                            "S %.0f%%  V %.0f%%"
                            % (saturation_step * 100.0, value_step * 100.0)
                        )
                        cell_color = hsva_to_rgba(
                            hue,
                            saturation_step,
                            value_step,
                            alpha,
                        )
                        slate.image_block(
                            self._swatch_params(
                                cell_color,
                                36.0,
                                18.0,
                            )
                        )
                        if slate.button("Set", self.params["button"]):
                            changed = (saturation_step, value_step)
                    finally:
                        slate.end_border()
            finally:
                slate.end_horizontal_stack()

        if changed is None:
            return None
        saturation, value = changed
        self.outline_picker_hsva = (hue, saturation, value, alpha)
        self._apply_outline_hsva()
        return changed

    def _draw_outline_color_picker(self):
        """Draw the fallback outline picker inline in the Look panel."""
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_border("ToolPanel.GroupBorder", self.params["color_popup"])
        try:
            slate.begin_vertical_stack(False)
            try:
                self._text("Outline Color", "accent")
                self._color_swatch(
                    self.state.adjust.outline_color,
                    width=322.0,
                    height=26.0,
                    tooltip="Current outline color",
                )

                hue, saturation, value, alpha = self.outline_picker_hsva
                changed = self._slider_float("Hue", hue, 0.0, 360.0, 1.0)
                if changed is not None:
                    self.outline_picker_hsva = (
                        changed,
                        saturation,
                        value,
                        alpha,
                    )
                    self._apply_outline_hsva()
                    hue = changed

                self._text("Saturation / Value", "muted")
                changed_sv = self._draw_outline_swatch_grid(
                    hue,
                    saturation,
                    value,
                    alpha,
                )
                if changed_sv is not None:
                    saturation, value = changed_sv

                changed = self._slider_float(
                    "Saturation", saturation, 0.0, 1.0, 0.01
                )
                if changed is not None:
                    saturation = changed
                    self.outline_picker_hsva = (
                        hue,
                        saturation,
                        value,
                        alpha,
                    )
                    self._apply_outline_hsva()
                changed = self._slider_float("Value", value, 0.0, 1.0, 0.01)
                if changed is not None:
                    value = changed
                    self.outline_picker_hsva = (
                        hue,
                        saturation,
                        value,
                        alpha,
                    )
                    self._apply_outline_hsva()

                hue, saturation, value, alpha = self.outline_picker_hsva
                self._text(
                    "S %.0f%%   V %.0f%%" % (saturation * 100.0, value * 100.0),
                    "muted",
                )
                changed = self._slider_float("Alpha", alpha, 0.0, 1.0, 0.01)
                if changed is not None:
                    self.outline_picker_hsva = (
                        hue,
                        saturation,
                        value,
                        changed,
                    )
                    self._apply_outline_hsva()

                self._text("Hex Color", "muted")
                changed_text = self._edit(self.outline_hex_edit, "#RRGGBBAA")
                if changed_text is not None:
                    self.outline_hex_edit = changed_text
                    parsed = parse_rgba_hex(changed_text)
                    if parsed is not None:
                        self._set_outline_color(parsed)
                if parse_rgba_hex(self.outline_hex_edit) is None:
                    self._text(
                        "Enter a color as #RRGGBB or #RRGGBBAA.",
                        "error",
                    )

                slate.begin_horizontal_stack(False)
                try:
                    if slate.button("Reset", self.params["button"]):
                        self._set_outline_color(AdjustState().outline_color)
                    if slate.button("Close", self.params["button"]):
                        self.outline_picker_open = False
                finally:
                    slate.end_horizontal_stack()
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_border()

        if slate.is_key_pressed(self.keys["Escape"]):
            self.outline_picker_open = False

    def _spin_int(self, label, value, minimum, maximum):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_horizontal_stack(False)
        try:
            self._text(label)
            result = slate.spin_box_int32(
                int(value),
                unreal.SlateIMSpinBoxInt32Params(
                    min=int(minimum), max=int(maximum)
                ),
            )
        finally:
            slate.end_horizontal_stack()
        if result is None:
            return None
        changed = int(result)
        return changed if changed != int(value) else None

    def _begin_section(self, title: str, subtitle: str = ""):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.padding(unreal.Margin(top=3.0, bottom=3.0))
        # Sections must consume the panel width. If they remain at desired
        # width, nested WrapBoxes only receive the width of their smallest
        # child and lay every button out on a separate row.
        slate.h_align(unreal.HorizontalAlignment.H_ALIGN_FILL)
        slate.begin_border("Brushes.Panel", self.params["section"])
        slate.begin_vertical_stack(False)
        self._text(title, "accent")
        if subtitle:
            self._text(subtitle, "muted")

    @staticmethod
    def _end_section():
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.end_vertical_stack()
        slate.end_border()

    def _naming_error(self) -> str:
        try:
            render_pattern(
                self.state.export.naming_pattern,
                source_path="/Game/Example/SM_Example",
                index=1,
                preset=self.state.export.preset_name,
                size=self.state.export.output_size,
            )
        except Exception as exc:
            return str(exc)
        return ""

    def _dirty(self, *, silhouette: bool = False):
        self.preview.mark_dirty(silhouette=bool(silhouette))

    def _set_source(self, source: CaptureSource):
        self.state.source = copy.deepcopy(source)
        self.capture_session.prepare_source(self.state.source)
        self.capture_session.frame_source(self.state.camera)
        self.source_error = ""
        self._log("Source: %s" % (source.display_name or source.key))
        self._dirty(silhouette=True)

    def _camera_changed(self):
        self._dirty(silhouette=True)

    def _poll_viewport(self, now: float):
        if (
            self.state.source is None
            or self.state.source.kind != SourceKind.WHOLE_VIEW
        ):
            return
        if now - self.last_viewport_poll < 0.10:
            return
        self.last_viewport_poll = now
        camera = self.capture_session.read_viewport_camera()
        if not camera:
            return
        location, rotation, fov = camera
        signature = (
            round(float(location.x), 3),
            round(float(location.y), 3),
            round(float(location.z), 3),
            round(float(rotation.pitch), 3),
            round(float(rotation.yaw), 3),
            round(float(rotation.roll), 3),
            round(float(fov), 3),
        )
        if signature != self.last_viewport_signature:
            self.last_viewport_signature = signature
            self._dirty(silhouette=True)

    def _ensure_rig(self):
        lighting = self.state.lighting
        if lighting.rig is None:
            lighting.rig = get_builtin_studio_rig(lighting.preset)
        return lighting.rig

    def _lighting_records(self):
        records = list(BUILTIN_LIGHTING_PRESETS)
        records.extend(
            (
                str(record.get("name") or identifier),
                CUSTOM_LIGHTING_PRESET_PREFIX + identifier,
            )
            for identifier, record in sorted(
                self.custom_lighting_presets.items(),
                key=lambda item: str(item[1].get("name", "")).lower(),
            )
        )
        return records

    def _lighting_display_name(self):
        preset = self.state.lighting.preset
        for label, identifier in BUILTIN_LIGHTING_PRESETS:
            if identifier == preset:
                return label
        if preset.startswith(CUSTOM_LIGHTING_PRESET_PREFIX):
            identifier = preset[len(CUSTOM_LIGHTING_PRESET_PREFIX) :]
            record = self.custom_lighting_presets.get(identifier, {})
            return str(record.get("name") or self.state.lighting.preset_name or "Custom")
        return "Neutral"

    def _apply_lighting_preset(self, preset_id: str):
        if preset_id.startswith(CUSTOM_LIGHTING_PRESET_PREFIX):
            identifier = preset_id[len(CUSTOM_LIGHTING_PRESET_PREFIX) :]
            record = self.custom_lighting_presets.get(identifier)
            if not record:
                return
            self.state.lighting = LightingState.from_dict(record.get("lighting"))
        else:
            self.state.lighting = LightingState(
                mode=LightingMode.STUDIO,
                preset=preset_id,
                rig=get_builtin_studio_rig(preset_id),
            )
        self.lighting_name_edit = self._lighting_display_name()
        self._dirty()

    def _draw_toolbar(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_border("Brushes.Header", self.params["toolbar"])
        try:
            slate.begin_horizontal_stack(False)
            try:
                source = self.state.source
                source_label = source.display_name if source else "No source selected"
                source_kind = (
                    SOURCE_KIND_LABELS.get(source.kind, "Asset")
                    if source
                    else "None"
                )
                source_path = (
                    source.paths[0]
                    if source and source.paths
                    else source_label
                )
                self._action_button("Use Selected Asset", "use_asset_selection")
                self._action_button("Use Selected Actors", "use_actor_selection")
                self._action_button("Use Current View", "use_whole_view")
                self._text("SOURCE", "muted")
                self._text(
                    "%s  [%s]" % (source_label, source_kind),
                    "text" if source else "warning",
                    tooltip=source_path,
                )
                self._action_button(
                    "Reload Selection",
                    "use_asset_selection",
                    tooltip="Use the active Content Browser selection.",
                )
                slate.fill()
                self._text(
                    "%d px / %dx"
                    % (
                        self.state.export.output_size,
                        self.state.export.supersample,
                    ),
                    "muted",
                )
                self._text(
                    "v%s%s"
                    % (
                        __version__,
                        "  UPDATE" if self.update_available else "",
                    ),
                    "success" if self.update_available else "muted",
                    tooltip=self.update_status,
                )
                self._action_button(
                    "Export Thumbnail",
                    "capture_export",
                    enabled=bool(source and not self.batch_mode),
                    tooltip="Export using the current Output settings.",
                )
            finally:
                slate.end_horizontal_stack()
        finally:
            slate.end_border()

    def _draw_camera_tab(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_scroll_box(self.params["scroll"])
        try:
            slate.begin_vertical_stack(False)
            try:
                self._begin_section("Camera Angles")
                try:
                    # HorizontalWrap collapses to one child's desired width in
                    # this UEFN SlateIM build. Use the same reliable horizontal
                    # rows as the Reset Camera controls, and recompute their
                    # column count from the live window width every frame.
                    recipe_names = tuple(FRAME_RECIPES)
                    column_count = _framing_recipe_column_count(
                        _window_client_width(), len(recipe_names)
                    )
                    for row_start in range(0, len(recipe_names), column_count):
                        slate.begin_horizontal_stack(False)
                        try:
                            for name in recipe_names[
                                row_start : row_start + column_count
                            ]:
                                self._action_button(name, "apply_recipe", name)
                        finally:
                            slate.end_horizontal_stack()
                finally:
                    self._end_section()

                self._begin_section("Camera Transform")
                try:
                    camera = self.state.camera
                    controls = (
                        ("Yaw", "yaw", -180.0, 180.0, 1.0),
                        ("Pitch", "pitch", -89.0, 89.0, 1.0),
                        ("Roll", "roll", -180.0, 180.0, 1.0),
                        ("Pan X", "pan_x", -5.0, 5.0, 0.02),
                        ("Pan Y", "pan_y", -5.0, 5.0, 0.02),
                        ("Zoom", "dolly", 0.02, 20.0, 0.02),
                        ("Field of View", "fov", 5.0, 120.0, 1.0),
                        ("Padding", "framing_margin", 1.0, 3.0, 0.02),
                    )
                    for label, name, minimum, maximum, step in controls:
                        changed = self._slider_float(
                            label,
                            getattr(camera, name),
                            minimum,
                            maximum,
                            step,
                        )
                        if changed is not None:
                            setattr(camera, name, changed)
                            self._camera_changed()
                    changed = self._checkbox(
                        "Auto-fit when source changes",
                        camera.auto_fit,
                        "Recompute framing when a different source is selected.",
                    )
                    if changed is not None:
                        camera.auto_fit = changed
                        self._dirty(silhouette=True)
                    slate.begin_horizontal_stack(False)
                    try:
                        self._action_button(
                            "Frame / Center (F)",
                            "frame_source",
                            enabled=self.state.source is not None,
                        )
                        self._action_button("Reset Camera", "reset_camera")
                    finally:
                        slate.end_horizontal_stack()
                finally:
                    self._end_section()
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_scroll_box()

    def _draw_lighting_tab(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_scroll_box(self.params["scroll"])
        try:
            slate.begin_vertical_stack(False)
            try:
                whole_view = bool(
                    self.state.source
                    and self.state.source.kind == SourceKind.WHOLE_VIEW
                )
                modes = ("Studio", "World")
                mode_index = 0 if self.state.lighting.mode == LightingMode.STUDIO else 1
                if whole_view:
                    self.state.lighting.mode = LightingMode.WORLD
                    self._text("Current View uses World lighting", "muted")
                else:
                    changed = self._combo(
                        modes, mode_index, key="lighting-mode"
                    )
                    if changed is not None:
                        next_mode = (
                            LightingMode.STUDIO if changed == 0 else LightingMode.WORLD
                        )
                        if (
                            next_mode == LightingMode.STUDIO
                            and self.state.lighting.rig is None
                        ):
                            self.state.lighting.rig = get_builtin_studio_rig(
                                self.state.lighting.preset
                            )
                        self.state.lighting.mode = next_mode
                        # A mode transition changes the render/exposure contract.
                        # Never reuse a highlight calibration measured under the
                        # previous lighting mode.
                        self.capture_session._studio_highlight_scale_cache.clear()
                        self._dirty()
                records = self._lighting_records()
                preset_ids = [identifier for _label, identifier in records]
                preset_index = preset_ids.index(self.state.lighting.preset) if self.state.lighting.preset in preset_ids else 0
                changed = self._combo(
                    [label for label, _identifier in records],
                    preset_index,
                    key="lighting-preset",
                    searchable=True,
                )
                if changed is not None:
                    self._apply_lighting_preset(records[changed][1])
                changed = self._slider_float(
                    "Brightness",
                    self.state.lighting.intensity,
                    0.1,
                    2.0,
                    0.05,
                )
                if changed is not None:
                    self.state.lighting.intensity = changed
                    self._dirty()
                changed_int = self._spin_int(
                    "Color Temperature (K)",
                    self.state.lighting.temperature_kelvin,
                    2500,
                    10000,
                )
                if changed_int is not None:
                    self.state.lighting.temperature_kelvin = changed_int
                    self._dirty()

                self._text("Custom Lighting Preset", "accent")
                changed_text = self._edit(self.lighting_name_edit, "Preset name")
                if changed_text is not None:
                    self.lighting_name_edit = changed_text[:64]
                slate.begin_horizontal_stack(False)
                try:
                    custom = self.state.lighting.preset.startswith(
                        CUSTOM_LIGHTING_PRESET_PREFIX
                    )
                    self._action_button("Save As", "lighting_save_as")
                    self._action_button("Save", "lighting_save", enabled=custom)
                    self._action_button("Rename", "lighting_rename", enabled=custom)
                    self._action_button("Delete", "lighting_delete", enabled=custom)
                finally:
                    slate.end_horizontal_stack()

                self._text("Studio Lights", "accent")
                role_index = self._combo(
                    ROLE_LABELS,
                    self.lighting_role_index,
                    key="lighting-role",
                )
                if role_index is not None:
                    self.lighting_role_index = role_index
                role = STUDIO_LIGHT_ROLES[self.lighting_role_index]
                rig = self._ensure_rig()
                light = getattr(rig, role)
                values = (
                    ("Intensity", "intensity_multiplier", 0.0, 3.0),
                    ("Size", "size_multiplier", 0.1, 5.0),
                )
                for label, name, minimum, maximum in values:
                    changed = self._slider_float(
                        label, getattr(light, name), minimum, maximum, 0.05
                    )
                    if changed is not None:
                        setattr(light, name, changed)
                        self._dirty()
                changed_int = self._spin_int(
                    "Temperature offset",
                    light.temperature_offset,
                    -4000,
                    4000,
                )
                if changed_int is not None:
                    light.temperature_offset = changed_int
                    self._dirty()
                position = list(light.position)
                for index, label in enumerate(("Toward camera", "Right", "Up")):
                    changed = self._slider_float(
                        label, position[index], -5.0, 5.0, 0.1
                    )
                    if changed is not None:
                        position[index] = changed
                        light.position = tuple(position)
                        self._dirty()
                changed = self._checkbox("Cast shadows", light.cast_shadows)
                if changed is not None:
                    light.cast_shadows = changed
                    self._dirty()
                self._action_button("Reset lighting", "reset_lighting")
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_scroll_box()

    def _draw_look_tab(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_scroll_box(self.params["scroll"])
        try:
            slate.begin_vertical_stack(False)
            try:
                self._begin_section("Color Corrections")
                adjust = self.state.adjust
                controls = (
                    ("Hue", "hue", -180.0, 180.0, 1.0),
                    ("Saturation", "saturation", 0.0, 3.0, 0.05),
                    ("Brightness", "brightness", 0.0, 3.0, 0.05),
                    ("Contrast", "contrast", 0.0, 3.0, 0.05),
                    ("Exposure", "exposure", -4.0, 4.0, 0.1),
                )
                try:
                    for label, name, minimum, maximum, step in controls:
                        changed = self._slider_float(
                            label, getattr(adjust, name), minimum, maximum, step
                        )
                        if changed is not None:
                            setattr(adjust, name, changed)
                            self._dirty()
                finally:
                    self._end_section()

                self._begin_section("Outline")
                try:
                    changed_int = self._spin_int(
                        "Width", adjust.outline_width, 0, 32
                    )
                    if changed_int is not None:
                        adjust.outline_width = changed_int
                        self._dirty(silhouette=False)
                    changed = self._checkbox(
                        "Outer Outline Only",
                        adjust.outer_silhouette_only,
                        "Outline only the outside edge and ignore enclosed holes.",
                    )
                    if changed is not None:
                        adjust.outer_silhouette_only = changed
                        self._dirty(silhouette=True)
                    slate.begin_horizontal_stack(False)
                    try:
                        slate.min_width(88.0)
                        slate.max_width(88.0)
                        self._text("Color")
                        slate.fill()
                        changed_text = self._edit(
                            self.outline_hex_edit, "#RRGGBBAA"
                        )
                    finally:
                        slate.end_horizontal_stack()
                    if changed_text is not None:
                        self.outline_hex_edit = changed_text
                        parsed = parse_rgba_hex(changed_text)
                        if parsed is not None:
                            self._set_outline_color(parsed)
                    if parse_rgba_hex(self.outline_hex_edit) is None:
                        self._text(
                            "Enter a color as #RRGGBB or #RRGGBBAA.",
                            "error",
                        )
                    if slate.button("Manual", self.params["button"]):
                        if not self.outline_picker_open:
                            self._sync_outline_picker()
                        self.outline_picker_open = not self.outline_picker_open
                    if self.outline_picker_open:
                        self._draw_outline_color_picker()
                finally:
                    self._end_section()

                self._begin_section("Comparison")
                try:
                    changed = self._checkbox(
                        "Show Original (B)",
                        self.state.ui.comparison_bypass,
                        "Compare with the unedited image without changing your settings.",
                    )
                    if changed is not None:
                        self.state.ui.comparison_bypass = changed
                    self._action_button("Reset Look", "reset_adjustments")
                finally:
                    self._end_section()
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_scroll_box()

    def _draw_output_tab(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_scroll_box(self.params["scroll"])
        try:
            slate.begin_vertical_stack(False)
            try:
                self._begin_section("Image")
                try:
                    sizes = (256, 512, 1024)
                    size_index = (
                        sizes.index(self.state.export.output_size)
                        if self.state.export.output_size in sizes
                        else 1
                    )
                    changed = self._combo(
                        ("256 × 256", "512 × 512", "1024 × 1024"),
                        size_index,
                        key="output-size",
                    )
                    if changed is not None:
                        self.state.export.output_size = sizes[changed]
                        self._dirty()
                    samples = (1, 2)
                    sample_index = (
                        samples.index(self.state.export.supersample)
                        if self.state.export.supersample in samples
                        else 1
                    )
                    changed = self._combo(
                        ("Standard Quality", "High Quality"),
                        sample_index,
                        key="output-supersample",
                    )
                    if changed is not None:
                        self.state.export.supersample = samples[changed]
                    changed = self._checkbox(
                        "Transparent Background",
                        self.state.export.transparent,
                    )
                    if changed is not None:
                        self.state.export.transparent = changed
                        self._dirty()
                    self._text("Background Color", "muted")
                    changed_text = self._edit(
                        self.background_hex_edit, "#RRGGBBAA"
                    )
                    if changed_text is not None:
                        self.background_hex_edit = changed_text
                        parsed = parse_rgba_hex(changed_text)
                        if parsed is not None:
                            self.state.export.background_color = parsed
                            self._dirty()
                    if parse_rgba_hex(self.background_hex_edit) is None:
                        self._text(
                            "Enter a color as #RRGGBB or #RRGGBBAA.",
                            "error",
                        )
                finally:
                    self._end_section()

                self._begin_section("Save Location")
                try:
                    changed_text = self._edit(
                        self.state.export.output_directory, self.store.root
                    )
                    if changed_text is not None:
                        self.state.export.output_directory = (
                            changed_text.strip() or self.store.root
                        )
                    if not os.path.isabs(self.state.export.output_directory):
                        self._text("Choose a complete folder path.", "error")
                    slate.begin_horizontal_stack(False)
                    try:
                        self._action_button("Default", "default_output")
                        self._action_button("Open Folder", "open_output")
                    finally:
                        slate.end_horizontal_stack()
                    self._text("File Name Pattern", "muted")
                    changed_text = self._edit(
                        self.state.export.naming_pattern,
                        "{name}_icon_{size}",
                        "Available tokens: {name}, {size}, {index}, {preset}.",
                    )
                    if changed_text is not None:
                        self.state.export.naming_pattern = (
                            changed_text or "{name}_icon_{size}"
                        )
                    naming_error = self._naming_error()
                    if naming_error:
                        self._text(naming_error, "error")
                finally:
                    self._end_section()

                self._begin_section("Import to Content Browser")
                try:
                    changed = self._checkbox(
                        "Create or update texture asset",
                        self.state.export.import_texture,
                    )
                    if changed is not None:
                        self.state.export.import_texture = changed
                    changed_text = self._edit(
                        self.state.export.import_path, self.default_import_path
                    )
                    if changed_text is not None:
                        self.state.export.import_path = (
                            changed_text.strip() or self.default_import_path
                        )
                    try:
                        validate_destination_path(
                            self.state.export.import_path,
                            self.project_content_root,
                        )
                    except ContentPathError as exc:
                        self._text(str(exc), "error")
                finally:
                    self._end_section()
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_scroll_box()

    def _draw_preview_tab(self, now: float):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        # The tab owns a resizable viewport. Preview controls live exclusively
        # in Preview Settings, so every available pixel can go to the GPU
        # preview row. ``maximize`` is a next-slot directive in SlateIM.
        slate.maximize()
        slate.begin_vertical_stack(False)
        try:
            if self.state.source is None:
                self._text("Select a source to start the preview", "warning")
            elif self.preview.material_instance is not None:
                slate.maximize()
                slate.begin_horizontal_stack(False)
                try:
                    slate.set_tool_tip(
                        "Left drag: orbit | Shift+drag: roll | Middle drag: pan | "
                        "Wheel: zoom | Ctrl+wheel: field of view | "
                        "Double-click/F: frame | B: show original"
                    )
                    # Fill the central slot; the 128/64 rail remains auto-sized
                    # on the right.  This is what makes the visible preview
                    # react to window and splitter resizing, not just its tab.
                    slate.maximize()
                    slate.image_material(
                        self.preview.material_instance,
                        unreal.Vector2D(float(PREVIEW_SIZE), float(PREVIEW_SIZE)),
                        self.params["image"],
                    )
                    hovered = bool(slate.is_hovered())
                    self._handle_preview_input(hovered, now)
                    slate.begin_vertical_stack(False)
                    try:
                        self._text("128 px", "muted")
                        slate.image_material(
                            self.preview.material_instance,
                            unreal.Vector2D(float(PREVIEW_SIZE), float(PREVIEW_SIZE)),
                            self.params["image128"],
                        )
                        self._text("64 px", "muted")
                        slate.image_material(
                            self.preview.material_instance,
                            unreal.Vector2D(float(PREVIEW_SIZE), float(PREVIEW_SIZE)),
                            self.params["image64"],
                        )
                    finally:
                        slate.end_vertical_stack()
                finally:
                    slate.end_horizontal_stack()
            diagnostics = self.preview.diagnostics()
            if self.state.ui.diagnostics_visible:
                self._begin_section("Diagnostics")
                try:
                    self._text(
                        "Slate %.1f FPS | GPU %.1f FPS | capture %.2f ms | %d frames | %s"
                        % (
                            self.measured_tick_fps(),
                            diagnostics["measured_fps"],
                            diagnostics["capture_scene_ms"],
                            diagnostics["capture_frames"],
                            "LIVE" if diagnostics["active"] else "IDLE",
                        ),
                        "muted",
                    )
                finally:
                    self._end_section()
            if diagnostics["last_error"]:
                self._text("Preview error: %s" % diagnostics["last_error"], "error")
            if (
                self.state.source is not None
                and self.state.adjust.outer_silhouette_only
                and self.state.adjust.outline_width > 0
                and not diagnostics.get("outer_mask_ready", False)
            ):
                self._text(
                    (
                        "Pause Live Preview to update the outer outline."
                        if self.state.live_preview
                        else "Updating outer outline..."
                    ),
                    "muted",
                )
            if diagnostics.get("outer_mask_last_error"):
                self._text(
                    "Outer silhouette error: %s"
                    % diagnostics["outer_mask_last_error"],
                    "error",
                )
            if self.source_error:
                self._text(self.source_error, "error")
        finally:
            slate.end_vertical_stack()

    def _handle_preview_input(self, hovered: bool, now: float):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        left = self.keys["LeftMouseButton"]
        middle = self.keys["MiddleMouseButton"]
        if hovered and slate.is_key_pressed(left):
            shift = slate.is_key_held(self.keys["LeftShift"]) or slate.is_key_held(self.keys["RightShift"])
            self.drag_mode = "roll" if shift else "orbit"
            if now - self.last_preview_click <= 0.30:
                self._queue("frame_source")
            self.last_preview_click = now
        elif hovered and slate.is_key_pressed(middle):
            self.drag_mode = "pan"

        # A release can happen after the pointer has left the image, and SlateIM
        # may then skip the one-frame ``is_key_released`` transition for this
        # widget.  Derive the drag lifetime from the persistent physical button
        # state instead, so a missed release event can never leave the camera
        # attached to the mouse cursor.
        drag_button = middle if self.drag_mode == "pan" else left
        if self.drag_mode and not slate.is_key_held(drag_button):
            self.drag_mode = ""
        if self.drag_mode:
            dx = float(slate.get_key_analog_value(self.keys["MouseX"]))
            dy = float(slate.get_key_analog_value(self.keys["MouseY"]))
            if abs(dx) > 1.0e-5 or abs(dy) > 1.0e-5:
                camera = self.state.camera
                if self.drag_mode == "orbit":
                    camera.yaw += dx * 0.35
                    camera.pitch = max(-89.0, min(89.0, camera.pitch - dy * 0.35))
                elif self.drag_mode == "pan":
                    camera.pan_x += dx * 0.004
                    camera.pan_y -= dy * 0.004
                else:
                    camera.roll += dx * 0.35
                self._camera_changed()
        if hovered:
            wheel = float(slate.get_key_analog_value(self.keys["MouseWheelAxis"]))
            if abs(wheel) > 1.0e-5:
                control = slate.is_key_held(self.keys["LeftControl"]) or slate.is_key_held(self.keys["RightControl"])
                if control:
                    self.state.camera.fov = max(
                        5.0, min(120.0, self.state.camera.fov - wheel * 2.0)
                    )
                else:
                    self.state.camera.dolly = max(
                        0.02,
                        min(20.0, self.state.camera.dolly * (0.88 ** wheel)),
                    )
                self._camera_changed()
            if slate.is_key_pressed(self.keys["F"]):
                self._queue("frame_source")
            if slate.is_key_pressed(self.keys["B"]):
                self.state.ui.comparison_bypass = not self.state.ui.comparison_bypass

    def _draw_batch_tab(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        # Match the stable Preview layout: the tab gives its next root widget
        # the full available slot, then the root stack reserves its remaining
        # height for the table. ``fill`` is only a spacer directive in this
        # SlateIM bridge and makes a vertical table oscillate while resizing.
        slate.maximize()
        self._draw_batch_content()

    def _draw_batch_content(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_vertical_stack(False)
        try:
            slate.begin_horizontal_stack(False)
            try:
                self._action_button("Load Selected Assets", "prepare_batch_assets")
                self._action_button("Load Selected Folder", "prepare_batch_folder")
            finally:
                slate.end_horizontal_stack()
            slate.begin_horizontal_stack(False)
            try:
                self._action_button(
                    "Cancel after current",
                    "cancel_batch",
                    enabled=bool(self.batch_mode),
                )
                self._action_button("Select All", "batch_select_all")
                self._action_button("Select None", "batch_select_none")
            finally:
                slate.end_horizontal_stack()
            slate.begin_horizontal_stack(False)
            try:
                changed_text = self._edit(self.batch_search, "Filter batch")
                if changed_text is not None:
                    self.batch_search = changed_text
                changed = self._combo(
                    ("All statuses", "Pending", "Done", "Failed"),
                    self.batch_status_index,
                    key="batch-status-filter",
                )
                if changed is not None:
                    self.batch_status_index = changed
            finally:
                slate.end_horizontal_stack()
            total = max(1, len(self.batch_queue) + self.batch_completed)
            percent = float(self.batch_completed) / float(total) if self.batch_mode else 0.0
            slate.progress_bar(percent, self.params["progress"])
            if self.batch_mode:
                elapsed = max(0.0, time.perf_counter() - self.batch_started_at)
                remaining_count = len(self.batch_queue)
                average = elapsed / max(1, self.batch_completed)
                self._text(
                    "%s: %d complete, %d remaining, ETA %.1fs"
                    % (
                        self.batch_mode.title(),
                        self.batch_completed,
                        remaining_count,
                        average * remaining_count,
                    )
                )
            slate.maximize()
            self._draw_batch_table()
            slate.begin_horizontal_stack(False)
            try:
                self._action_button(
                    "Export Checked",
                    "start_batch_export",
                    enabled=bool(
                        not self.batch_mode
                        and any(row.checked for row in self.batch_rows)
                    ),
                )
                self._action_button(
                    "Retry Failures",
                    "retry_batch",
                    enabled=any(row.status == "failed" for row in self.batch_rows),
                )
            finally:
                slate.end_horizontal_stack()
            if self.batch_ignored:
                self._text(
                    "Ignored %d: %s"
                    % (len(self.batch_ignored), ", ".join(self.batch_ignored[:4]))
                )
        finally:
            slate.end_vertical_stack()

    def _draw_batch_table(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_vertical_stack(False)
        try:
            slate.begin_border("Brushes.Header", self.params["toolbar"])
            try:
                slate.begin_horizontal_stack(False)
                try:
                    slate.min_width(62.0)
                    slate.max_width(62.0)
                    self._text("Export", "muted")
                    slate.fill()
                    self._text("Source", "muted")
                    slate.min_width(94.0)
                    slate.max_width(94.0)
                    self._text("Status", "muted")
                    slate.fill()
                    self._text("PNG", "muted")
                finally:
                    slate.end_horizontal_stack()
            finally:
                slate.end_border()

            # Only the rows scroll. The header stays visible and both flexible
            # columns receive an equal share of the live panel width.
            slate.maximize()
            slate.begin_scroll_box(self.params["scroll"])
            try:
                slate.begin_vertical_stack(False)
                try:
                    visible = self._visible_batch_rows()
                    if not visible:
                        self._text("No assets loaded.", "muted")
                    for index, row in visible:
                        slate.begin_border("Brushes.Panel", self.params["card"])
                        try:
                            slate.begin_horizontal_stack(False)
                            try:
                                slate.min_width(62.0)
                                slate.max_width(62.0)
                                changed = slate.check_box(
                                    row.checked,
                                    unreal.SlateIMCheckBoxParams(
                                        label=str(index + 1)
                                    ),
                                )
                                if changed is not None:
                                    row.checked = bool(changed)

                                source_path = (
                                    row.source.paths[0]
                                    if row.source.paths
                                    else row.source.display_name
                                )
                                source_label = (
                                    str(source_path).replace("\\", "/").rsplit("/", 1)[-1]
                                    or row.source.display_name
                                )
                                slate.fill()
                                self._text(
                                    source_label,
                                    tooltip=source_path,
                                )

                                slate.min_width(94.0)
                                slate.max_width(94.0)
                                status_text = row.status.title()
                                status_tooltip = (
                                    row.status
                                    + ((": " + row.error) if row.error else "")
                                )
                                style = (
                                    "success"
                                    if row.status == "done"
                                    else "error"
                                    if row.status == "failed"
                                    else "warning"
                                    if row.status == "capturing"
                                    else "muted"
                                )
                                self._text(
                                    status_text,
                                    style,
                                    tooltip=status_tooltip,
                                )

                                png_path = (
                                    getattr(row.result, "png_path", "")
                                    if row.result
                                    else ""
                                )
                                png_label = (
                                    os.path.basename(png_path)
                                    if png_path
                                    else "—"
                                )
                                slate.fill()
                                self._text(
                                    png_label,
                                    "muted",
                                    tooltip=png_path,
                                )
                            finally:
                                slate.end_horizontal_stack()
                        finally:
                            slate.end_border()
                finally:
                    slate.end_vertical_stack()
            finally:
                slate.end_scroll_box()
        finally:
            slate.end_vertical_stack()

    def _visible_batch_rows(self):
        query = self.batch_search.strip().casefold()
        wanted = ("", "pending", "done", "failed")[self.batch_status_index]
        visible = []
        for index, row in enumerate(self.batch_rows):
            text = " ".join(row.source.paths or [row.source.display_name]).casefold()
            if query and query not in text:
                continue
            if wanted and row.status != wanted:
                continue
            visible.append((index, row))
        return visible

    def _draw_left_tabs(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_tab_stack()
        try:
            for title, tab_id, draw in (
                (
                    "Camera",
                    INSPECTOR_TAB_IDS["Camera"],
                    self._draw_camera_tab,
                ),
                (
                    "Lighting",
                    INSPECTOR_TAB_IDS["Lighting"],
                    self._draw_lighting_tab,
                ),
                (
                    "Look",
                    INSPECTOR_TAB_IDS["Look"],
                    self._draw_look_tab,
                ),
                (
                    "Output",
                    INSPECTOR_TAB_IDS["Output"],
                    self._draw_output_tab,
                ),
                (
                    "Batch",
                    BATCH_TAB_ID,
                    self._draw_batch_tab,
                ),
            ):
                active = slate.begin_tab(
                    tab_id, self.params["tabs"][title]
                )
                try:
                    if active:
                        slate.maximize()
                        draw()
                finally:
                    slate.end_tab()
        finally:
            slate.end_tab_stack()

    def _draw_preview_stack(self, now: float):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_tab_stack()
        try:
            active = slate.begin_tab(PREVIEW_TAB_ID, self.params["tabs"]["Preview"])
            try:
                if active:
                    slate.maximize()
                    self._draw_preview_tab(now)
            finally:
                slate.end_tab()
        finally:
            slate.end_tab_stack()

    def _draw_preview_settings_stack(self, now: float):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_tab_stack()
        try:
            active = slate.begin_tab(
                PREVIEW_SETTINGS_TAB_ID,
                self.params["tabs"]["Preview Settings"],
            )
            try:
                if active:
                    slate.maximize()
                    self._draw_preview_settings_tab(now)
            finally:
                slate.end_tab()
        finally:
            slate.end_tab_stack()

    def _draw_preview_settings_tab(self, now: float):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_scroll_box(self.params["scroll"])
        try:
            slate.begin_vertical_stack(False)
            try:
                self._begin_section("Preview")
                try:
                    background_index = PREVIEW_BACKGROUNDS.index(
                        self.state.preview_background
                    )
                    changed = self._combo(
                        PREVIEW_BACKGROUNDS,
                        background_index,
                        key="preview-settings-background",
                    )
                    if changed is not None:
                        self.state.preview_background = PREVIEW_BACKGROUNDS[changed]
                        self._dirty()

                    changed = self._checkbox(
                        "Live Preview", self.state.live_preview
                    )
                    if changed is not None:
                        self.state.live_preview = changed
                        self.preview.set_live(changed, now)

                    self._action_button(
                        "Frame Source (F)",
                        "frame_source",
                        enabled=self.state.source is not None,
                    )
                finally:
                    self._end_section()

                self._begin_section("Composition")
                try:
                    changed = self._checkbox(
                        "Show Original (B)", self.state.ui.comparison_bypass
                    )
                    if changed is not None:
                        self.state.ui.comparison_bypass = changed
                        self._dirty()

                    changed = self._checkbox(
                        "Rule of Thirds", self.state.ui.overlay_thirds
                    )
                    if changed is not None:
                        self.state.ui.overlay_thirds = changed
                        self._dirty()

                    changed = self._checkbox(
                        "Center Guide", self.state.ui.overlay_center
                    )
                    if changed is not None:
                        self.state.ui.overlay_center = changed
                        self._dirty()

                    safe_values = (0, 80, 90)
                    safe_index = safe_values.index(self.state.ui.safe_frame)
                    changed = self._combo(
                        ("Safe Area Off", "Safe Area 80%", "Safe Area 90%"),
                        safe_index,
                        key="preview-settings-safe-frame",
                    )
                    if changed is not None:
                        self.state.ui.safe_frame = safe_values[changed]
                        self._dirty()
                finally:
                    self._end_section()

                self._begin_section("Display")
                try:
                    changed = self._checkbox(
                        "Show Performance Details",
                        self.state.ui.diagnostics_visible,
                    )
                    if changed is not None:
                        self.state.ui.diagnostics_visible = changed
                finally:
                    self._end_section()

                self._begin_section("Updates")
                try:
                    self._text("Thumbnail Creator v%s" % __version__, "accent")
                    self._text(self.update_status, "warning" if self.update_available else "muted")

                    changed = self._checkbox(
                        "Check on launch",
                        self.update_settings.get("check_updates_on_launch", True),
                        tooltip="Checks version metadata only; it does not download code.",
                    )
                    if changed is not None:
                        self.update_settings["check_updates_on_launch"] = changed
                        if not changed:
                            self.update_settings["auto_install_updates"] = False
                        save_update_settings(self.update_settings)

                    changed = self._checkbox(
                        "Auto-install verified updates",
                        self.update_settings.get("auto_install_updates", False),
                        tooltip=(
                            "Opt-in. Downloads only tagged releases whose embedded "
                            "version matches and whose Python code compiles."
                        ),
                    )
                    if changed is not None:
                        self.update_settings["auto_install_updates"] = changed
                        if changed:
                            self.update_settings["check_updates_on_launch"] = True
                        save_update_settings(self.update_settings)

                    slate.begin_horizontal_stack(False)
                    try:
                        self._action_button(
                            "Check Now",
                            "check_updates",
                            enabled=not self.update_worker_running,
                        )
                        if self.update_available:
                            self._action_button(
                                "Install v%s" % self.update_available,
                                "install_update",
                                enabled=not self.update_worker_running,
                            )
                        self._action_button("Release Page", "open_release_page")
                    finally:
                        slate.end_horizontal_stack()

                    if self.update_available and self.update_changelog:
                        self._text("What's new", "accent")
                        note_lines = self.update_changelog.splitlines()
                        for line in note_lines[:16]:
                            if line.strip():
                                self._text(line[:96], "muted")
                        if len(note_lines) > 16:
                            self._text("More notes are available on the release page.", "muted")
                finally:
                    self._end_section()
            finally:
                slate.end_vertical_stack()
        finally:
            slate.end_scroll_box()

    def draw(self, now: float):
        """Draw the stable three-column workspace used by Thumbnail Creator."""
        self._pump_update_events()
        slate = unreal.SlateIMBlueprintFunctionLibrary
        # ``maximize`` applies to the *next* SlateIM slot.  It must therefore
        # target the root stack as well as the workspace below the toolbar;
        # otherwise the whole UI keeps its initial desired width when the
        # floating window (or a docked host) is enlarged.
        slate.maximize()
        slate.begin_vertical_stack(False)
        try:
            slate.auto_size()
            self._draw_toolbar()
            slate.maximize()
            slate.begin_tab_group(TAB_GROUP_ID)
            try:
                slate.begin_tab_splitter(unreal.Orientation.ORIENT_HORIZONTAL)
                try:
                    slate.tab_splitter_size_coefficient(0.28)
                    self._draw_left_tabs()
                    slate.tab_splitter_size_coefficient(0.52)
                    self._draw_preview_stack(now)
                    slate.tab_splitter_size_coefficient(0.20)
                    self._draw_preview_settings_stack(now)
                finally:
                    slate.end_tab_splitter()
            finally:
                slate.end_tab_group()
        finally:
            slate.end_vertical_stack()

    def _normalize_import_path(self):
        normalized = project_destination_or_default(
            self.state.export.import_path,
            self.project_content_root,
            self.default_import_path,
        )
        changed = normalized != self.state.export.import_path
        self.state.export.import_path = normalized
        return changed

    def _request(self, source=None):
        if self.state.export.import_texture:
            self._normalize_import_path()
        return self.state.capture_request(preview=False, source=source)

    def _output_for_source(self, source, index=1, reserved=None):
        source_path = source.paths[0] if source.paths else source.display_name
        stem = render_pattern(
            self.state.export.naming_pattern,
            source_path=source_path,
            index=index,
            preset=self.state.export.preset_name,
            size=self.state.export.output_size,
        )
        return unique_png_path(
            self.state.export.output_directory or self.store.root,
            stem,
            reserved,
        )

    def _capture_export_source(
        self,
        source,
        output,
        *,
        update_active_camera=True,
    ):
        request = self._request(source)
        self.preview.suspend(True)
        try:
            frame = self.capture_session.capture(request, output)
            fitted = frame.result.metadata.get("fitted_camera")
            if (
                update_active_camera
                and fitted
                and source.key
                == (self.state.source.key if self.state.source else "")
            ):
                self.state.camera = CameraState(**fitted)
            texture = ""
            if request.export.import_texture:
                asset_name = safe_name(os.path.splitext(os.path.basename(output))[0])
                import_info = import_or_reimport_texture(
                    output, request.export.import_path, asset_name
                )
                texture = import_info["texture_path"]
            frame.result.texture_path = texture
            return frame
        finally:
            self.preview.suspend(False)
            self.preview.mark_dirty()

    def _confirm(self, text: str) -> bool:
        result = unreal.SlateIMBlueprintFunctionLibrary.modal_dialog(
            unreal.AppMsgType.YES_NO,
            text,
            self.params["modal"],
        )
        return result == unreal.AppReturnType.YES

    def _capture_from_active_source(self):
        if self.state.source is None:
            raise CaptureError("Select a source before exporting.")
        output = self._output_for_source(self.state.source)
        frame = self._capture_export_source(self.state.source, output)
        self._log(
            "DONE: %s%s"
            % (
                output,
                " -> " + frame.result.texture_path
                if frame.result.texture_path
                else "",
            )
        )
        self.save_session()

    def process_action(self):
        if self.action_in_progress or not self.pending_actions:
            return
        action, payload = self.pending_actions.pop(0)
        self.action_in_progress = True
        try:
            if action == "use_asset_selection":
                sources = selected_asset_sources()
                if not sources:
                    raise CaptureError("Select a supported asset in the Content Browser.")
                self._set_source(sources[0])
            elif action == "use_actor_selection":
                source = selected_actor_source()
                if source is None:
                    raise CaptureError("Select one or more level actors.")
                self._set_source(source)
            elif action == "use_whole_view":
                self._set_source(
                    CaptureSource(SourceKind.WHOLE_VIEW, [], "Current View")
                )
            elif action == "apply_recipe":
                yaw, pitch, roll = FRAME_RECIPES[str(payload)]
                self.state.camera.yaw = yaw
                self.state.camera.pitch = pitch
                self.state.camera.roll = roll
                self.state.camera.pan_x = 0.0
                self.state.camera.pan_y = 0.0
                self.state.camera.dolly = 1.0
                self._camera_changed()
            elif action == "frame_source":
                self.capture_session.frame_source(self.state.camera)
                self._camera_changed()
            elif action == "capture_export":
                self._capture_from_active_source()
            elif action == "default_output":
                self.state.export.output_directory = self.store.root
            elif action == "open_output":
                path = self.state.export.output_directory or self.store.root
                os.makedirs(path, exist_ok=True)
                subprocess.Popen(["explorer.exe", os.path.normpath(path)])
            elif action == "reset_adjustments":
                self.state.adjust = AdjustState()
                self.outline_hex_edit = format_rgba_hex(self.state.adjust.outline_color)
                self._sync_outline_picker()
                self._dirty()
            elif action == "reset_camera":
                self.state.camera = CameraState()
                if self.state.source is not None:
                    self.capture_session.frame_source(self.state.camera)
                self._camera_changed()
            elif action == "reset_lighting":
                self.state.lighting = LightingState(
                    mode=LightingMode.WORLD,
                    preset="neutral",
                    rig=get_builtin_studio_rig("neutral"),
                )
                self.lighting_name_edit = "Neutral"
                self._dirty()
            elif action.startswith("lighting_"):
                self._process_lighting_action(action)
            elif action == "prepare_batch_assets":
                self._prepare_batch(selected_asset_sources(), [])
            elif action == "prepare_batch_folder":
                sources, ignored = selected_folder_sources(True)
                self._prepare_batch(sources, ignored)
            elif action == "start_batch_export":
                self._start_batch_export()
            elif action == "retry_batch":
                self._start_batch_retry()
            elif action == "cancel_batch":
                self.batch_cancel = True
            elif action == "batch_select_all":
                for row in self.batch_rows:
                    row.checked = True
            elif action == "batch_select_none":
                for row in self.batch_rows:
                    row.checked = False
            elif action == "check_updates":
                self.update_payload = None
                self.update_available = ""
                self.update_changelog = ""
                self._start_update_check()
            elif action == "install_update":
                if not self.update_available:
                    raise RuntimeError("No update is currently available.")
                prompt = "Install Thumbnail Creator v%s?\n\n%s" % (
                    self.update_available,
                    (self.update_changelog or "Release notes are unavailable.")[:2400],
                )
                if self._confirm(prompt):
                    if self.update_payload is not None:
                        self._queue("apply_update")
                    else:
                        self._start_update_download(apply_when_ready=True)
            elif action == "apply_update":
                update = self.update_payload
                if update is None:
                    raise RuntimeError("The verified update payload is missing.")
                self.update_status = "Installing v%s..." % update["version"]
                apply_validated_update(update)
            elif action == "open_release_page":
                import webbrowser

                url = (
                    "%s/releases/tag/%s%s"
                    % (_REPO_URL, _TAG_PREFIX, self.update_available)
                    if self.update_available
                    else _REPO_URL + "/releases"
                )
                webbrowser.open(url)
        except Exception as exc:
            self._log("ERROR: %s" % exc)
            unreal.log_error("[ThumbnailCreator] %s: %s" % (action, exc))
        finally:
            self.action_in_progress = False

    def _process_lighting_action(self, action: str):
        name = self.lighting_name_edit.strip()
        preset = self.state.lighting.preset
        identifier = (
            preset[len(CUSTOM_LIGHTING_PRESET_PREFIX) :]
            if preset.startswith(CUSTOM_LIGHTING_PRESET_PREFIX)
            else ""
        )
        if action == "lighting_save_as":
            record = self.store.create_lighting_preset(
                name, self.state.lighting.to_dict()
            )
            self.custom_lighting_presets = self.store.load_lighting_presets()
            self.state.lighting = LightingState.from_dict(record["lighting"])
        elif action == "lighting_save":
            if not identifier:
                raise ValueError("Use Save As for a built-in lighting preset.")
            record = self.store.update_lighting_preset(
                identifier, lighting=self.state.lighting.to_dict()
            )
            self.custom_lighting_presets = self.store.load_lighting_presets()
            self.state.lighting = LightingState.from_dict(record["lighting"])
        elif action == "lighting_rename":
            if not identifier:
                raise ValueError("Built-in lighting presets cannot be renamed.")
            record = self.store.rename_lighting_preset(identifier, name)
            self.custom_lighting_presets = self.store.load_lighting_presets()
            self.state.lighting = LightingState.from_dict(record["lighting"])
        elif action == "lighting_delete":
            if not identifier:
                raise ValueError("Built-in lighting presets cannot be deleted.")
            if self._confirm("Delete lighting preset '%s'?" % name):
                self.store.delete_lighting_preset(identifier)
                self.custom_lighting_presets = self.store.load_lighting_presets()
                self._apply_lighting_preset("neutral")
        self.lighting_name_edit = self._lighting_display_name()
        self._dirty()

    def _prepare_batch(self, sources, ignored):
        if not sources:
            raise CaptureError("No supported assets were found in the selection.")
        self.batch_rows = [BatchRow(copy.deepcopy(source)) for source in sources]
        self.batch_ignored = list(ignored)
        self.batch_queue = []
        self.batch_mode = ""
        self.batch_cancel = False
        self.batch_started_at = 0.0
        self.batch_completed = 0
        self.batch_reserved = set()
        self._set_source(self.batch_rows[0].source)
        self._log(
            "Batch loaded: %d asset(s), no captures generated."
            % len(self.batch_rows)
        )

    def _start_batch_export(self):
        queue = [
            index
            for index, row in enumerate(self.batch_rows)
            if row.checked
        ]
        if not queue:
            raise ValueError("No batch rows are checked.")
        for index in queue:
            self.batch_rows[index].status = "pending"
            self.batch_rows[index].error = ""
        self.batch_queue = queue
        self.batch_mode = "export"
        self.batch_cancel = False
        self.batch_started_at = time.perf_counter()
        self.batch_completed = 0
        self.batch_reserved = set()

    def _start_batch_retry(self):
        queue = [
            index
            for index, row in enumerate(self.batch_rows)
            if row.status == "failed"
        ]
        if not queue:
            raise ValueError("There are no failed batch rows to retry.")
        self.batch_queue = queue
        self.batch_mode = "export"
        self.batch_cancel = False
        self.batch_started_at = time.perf_counter()
        self.batch_completed = 0

    def process_batch_step(self):
        if not self.batch_mode or self.action_in_progress:
            return
        if self.batch_cancel or not self.batch_queue:
            status = "cancelled" if self.batch_cancel else "complete"
            self._log("Batch %s: %d item(s)." % (status, self.batch_completed))
            self.batch_mode = ""
            self.batch_queue = []
            return
        index = self.batch_queue.pop(0)
        row = self.batch_rows[index]
        self.action_in_progress = True
        row.status = "capturing"
        try:
            output = self._output_for_source(
                row.source,
                index + 1,
                self.batch_reserved,
            )
            frame = self._capture_export_source(
                row.source,
                output,
                update_active_camera=False,
            )
            row.result = frame.result
            row.status = "done"
            row.error = ""
            self.batch_completed += 1
        except Exception as exc:
            row.status = "failed"
            row.error = str(exc)
            row.checked = False
            self.batch_completed += 1
        finally:
            self.action_in_progress = False

    def tick_preview(self, now: float):
        self._poll_viewport(now)
        request = None
        if self.state.source is not None:
            try:
                request = self.state.capture_request(preview=True)
            except Exception as exc:
                self.source_error = str(exc)
        self.preview.tick(
            request,
            self.state.preview_background,
            now,
            ui_overlays={
                "comparison_bypass": self.state.ui.comparison_bypass,
                "overlay_thirds": self.state.ui.overlay_thirds,
                "overlay_center": self.state.ui.overlay_center,
                "safe_frame": self.state.ui.safe_frame,
            },
        )

    def record_tick(self, now: float):
        self.tick_times.append(float(now))
        self.tick_times = self.tick_times[-90:]

    def measured_tick_fps(self) -> float:
        if len(self.tick_times) < 2:
            return 0.0
        elapsed = self.tick_times[-1] - self.tick_times[0]
        return (len(self.tick_times) - 1) / elapsed if elapsed > 0.0 else 0.0

    def save_session(self):
        self.store.save_session(self.state.to_dict())

    def diagnostics(self):
        return {
            "window_root_id": WINDOW_ROOT_ID,
            "host": "floating_slate_window",
            "native_slate": True,
            "py_side": False,
            "closed": self.closed,
            "source": self.state.source.key if self.state.source else "",
            "pending_actions": len(self.pending_actions),
            "batch_mode": self.batch_mode,
            "store_root": self.store.root,
            "slate_tick_fps": self.measured_tick_fps(),
            "preview": self.preview.diagnostics(),
        }

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.save_session()
        except Exception as exc:
            unreal.log_warning("[ThumbnailCreator] Session save failed: %s" % exc)
        try:
            self.preview.cleanup()
        finally:
            self.capture_session.cleanup()


def _draw_frame(_delta_time: float):
    app = getattr(_REGISTRY, "app", None)
    if app is None or app.closed:
        return
    slate = unreal.SlateIMBlueprintFunctionLibrary
    if not slate.can_update_slate_im():
        return
    now = time.perf_counter()
    app.record_tick(now)
    root_attempted = False
    try:
        was_open = bool(_REGISTRY.window_open)
        root_attempted = True
        began = slate.begin_window_root(WINDOW_ROOT_ID, _window_params())
        _REGISTRY.reopen_requested = False
        _REGISTRY.window_open = bool(began)
        _REGISTRY.ever_opened = bool(_REGISTRY.ever_opened or began)
        if began:
            app.draw(now)
        elif was_open and _REGISTRY.ever_opened:
            _REGISTRY.cleanup_requested = True
        _REGISTRY.last_error = ""
    except Exception as exc:
        _REGISTRY.last_error = repr(exc)
        unreal.log_error("[ThumbnailCreator.Slate] %s" % exc)
    finally:
        if root_attempted:
            try:
                slate.end_root()
            except Exception as exc:
                _REGISTRY.last_error = "%s | end_root: %r" % (
                    _REGISTRY.last_error,
                    exc,
                )
    app.process_action()
    app.process_batch_step()
    app.tick_preview(now)


def _tick(delta_time: float):
    if _REGISTRY.cleanup_requested:
        close_window()
        return
    draw = getattr(_REGISTRY, "draw_frame", None)
    if draw is not None:
        draw(delta_time)


def open_window():
    existing = getattr(_REGISTRY, "app", None)
    if existing is not None and not existing.closed:
        if getattr(_REGISTRY, "app_build_token", None) == APP_BUILD_TOKEN:
            _REGISTRY.reopen_requested = True
            return diagnostics()
        close_window()
    app = ThumbnailCreatorSlateApp()
    _REGISTRY.app = app
    _REGISTRY.app_build_token = APP_BUILD_TOKEN
    _REGISTRY.draw_frame = _draw_frame
    _REGISTRY.reopen_requested = True
    _REGISTRY.cleanup_requested = False
    _REGISTRY.ever_opened = False
    _REGISTRY.last_error = ""
    if getattr(_REGISTRY, "tick_handle", None) is None:
        _REGISTRY.tick_callback = _tick
        _REGISTRY.tick_handle = unreal.register_slate_post_tick_callback(_tick)
    unreal.log("[ThumbnailCreator] Native SlateIM workspace opened.")
    return diagnostics()


def close_window():
    app = getattr(_REGISTRY, "app", None)
    _REGISTRY.cleanup_requested = False
    _REGISTRY.window_open = False
    if app is not None:
        app.close()
    _REGISTRY.app = None
    handle = getattr(_REGISTRY, "tick_handle", None)
    _REGISTRY.tick_handle = None
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception as exc:
            unreal.log_warning("[ThumbnailCreator] Tick cleanup failed: %s" % exc)
    _REGISTRY.tick_callback = None
    unreal.log("[ThumbnailCreator] Native SlateIM workspace closed.")
    return diagnostics()


def diagnostics():
    app = getattr(_REGISTRY, "app", None)
    payload = app.diagnostics() if app is not None else {
        "window_root_id": WINDOW_ROOT_ID,
        "host": "floating_slate_window",
        "native_slate": True,
        "py_side": False,
        "closed": True,
    }
    payload.update(
        {
            "window_open": bool(_REGISTRY.window_open),
            "tick_registered": getattr(_REGISTRY, "tick_handle", None) is not None,
            "last_error": str(_REGISTRY.last_error),
        }
    )
    return payload
# ============================================================================
# Launcher
# ============================================================================


MODULE_DIR = os.path.dirname(SCRIPT_PATH)
FULL_MENU_ENTRY = "UEFNPythonTools.ThumbnailCreator"
LEGACY_FULL_MENU_ENTRY = "ThumbnailCreator"
TOOLS_MENU = "LevelEditor.MainMenu.Tools"
TOOLS_SECTION = "ThumbnailCreator"


def _message(kind: str, message: str) -> None:
    try:
        unreal.EditorDialog.show_message(
            TOOL_NAME,
            message,
            unreal.AppMsgType.OK,
        )
    except Exception:
        logger = "error" if kind == "critical" else "warning"
        getattr(unreal, "log_%s" % logger)(message)


DEPENDENCY_SETUP_ROOT_ID = "UEFNPythonTools.ThumbnailCreator.Setup"
_DEPENDENCY_SETUP = None


class _DependencySetupController:
    """Small dependency installer rendered entirely with UEFN's native SlateIM."""

    def __init__(
        self,
        required_names: list[str],
        python_exe: str,
        target: str,
    ):
        import queue
        import threading

        self.required_names = list(required_names)
        self.python_exe = python_exe
        self.target = target
        self.events = queue.Queue()
        self.logs: list[str] = []
        self.status = "Preparing the embedded Python environment..."
        self.running = False
        self.done = False
        self.ok = False
        self.error = ""
        self.ever_opened = False
        self.reopen_requested = True
        self.button_latches: set[str] = set()
        self.tick_handle = None
        self._threading = threading
        self.text_params = unreal.SlateIMTextParams()
        self.button_params = unreal.SlateIMButtonParams(enabled=True)
        self.scroll_params = unreal.SlateIMScrollBoxParams()
        self.progress_params = unreal.SlateIMProgressBarParams()
        self.start()

    def start(self):
        if self.running:
            return
        self.running = True
        self.done = False
        self.ok = False
        self.error = ""
        self.status = "Checking pip..."
        self.logs.append("=== Thumbnail Creator setup ===")

        def worker():
            try:
                target = install_runtime_dependencies(
                    self.required_names,
                    python_exe=self.python_exe,
                    target=self.target,
                    on_line=lambda line: self.events.put(("log", line)),
                )
                self.events.put(("success", target))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self._threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        import queue

        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    line = str(value)
                    self.logs.append(line)
                    self.logs = self.logs[-120:]
                    lowered = line.lower()
                    if "collecting" in lowered or "downloading" in lowered:
                        self.status = line
                    elif "installing collected" in lowered:
                        self.status = "Installing Pillow..."
                elif kind == "success":
                    self.running = False
                    self.done = True
                    self.ok = True
                    self.status = "Pillow is ready. Opening Thumbnail Creator..."
                    self.logs.append("Installed in %s" % value)
                elif kind == "error":
                    self.running = False
                    self.done = True
                    self.ok = False
                    self.error = str(value)
                    self.status = "Setup failed: %s" % value
        except queue.Empty:
            pass

    def pressed(self, label: str) -> bool:
        value = bool(
            unreal.SlateIMBlueprintFunctionLibrary.button(label, self.button_params)
        )
        if value and label not in self.button_latches:
            self.button_latches.add(label)
            return True
        if not value:
            self.button_latches.discard(label)
        return False

    def draw(self):
        slate = unreal.SlateIMBlueprintFunctionLibrary
        slate.begin_vertical_stack(False)
        try:
            slate.text("Preparing Thumbnail Creator", self.text_params)
            slate.text(self.status, self.text_params)
            if self.running:
                slate.progress_bar(
                    (time.perf_counter() * 0.35) % 1.0,
                    self.progress_params,
                )
            slate.begin_scroll_box(self.scroll_params)
            try:
                slate.begin_vertical_stack(False)
                try:
                    for line in self.logs[-30:]:
                        slate.text(line, self.text_params)
                finally:
                    slate.end_vertical_stack()
            finally:
                slate.end_scroll_box()
            if self.done and not self.ok:
                slate.begin_horizontal_stack(False)
                try:
                    if self.pressed("Retry"):
                        self.start()
                    if self.pressed("Close"):
                        self.error = "__closed__"
                finally:
                    slate.end_horizontal_stack()
        finally:
            slate.end_vertical_stack()


def _dependency_setup_window_params(controller):
    size = unreal.Vector2f()
    size.set_editor_property("x", 720.0)
    size.set_editor_property("y", 460.0)
    params = unreal.SlateIMWindowParams(
        window_title="Thumbnail Creator - Setup",
        should_reopen=bool(controller.reopen_requested),
        always_on_top=True,
    )
    params.set_editor_property("window_size", size)
    return params


def _stop_dependency_setup(*, open_tool: bool = False):
    global _DEPENDENCY_SETUP
    controller = _DEPENDENCY_SETUP
    _DEPENDENCY_SETUP = None
    if controller is None:
        return
    handle = controller.tick_handle
    controller.tick_handle = None
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
    if open_tool:
        try:
            _load_pillow_modules()
            register_tools_menu()
            open_window()
        except Exception as exc:
            _message("critical", "Pillow installed, but startup failed:\n\n%s" % exc)


def _dependency_setup_tick(_delta_time: float):
    controller = _DEPENDENCY_SETUP
    if controller is None:
        return
    controller.pump()
    slate = unreal.SlateIMBlueprintFunctionLibrary
    root_attempted = False
    began = False
    try:
        if not slate.can_update_slate_im():
            return
        root_attempted = True
        began = bool(
            slate.begin_window_root(
                DEPENDENCY_SETUP_ROOT_ID,
                _dependency_setup_window_params(controller),
            )
        )
        controller.reopen_requested = False
        controller.ever_opened = bool(controller.ever_opened or began)
        if began:
            controller.draw()
    finally:
        if root_attempted:
            try:
                slate.end_root()
            except Exception:
                pass
    if controller.ok:
        _stop_dependency_setup(open_tool=True)
    elif controller.error == "__closed__" or (
        controller.ever_opened and not began
    ):
        _stop_dependency_setup(open_tool=False)


def _start_dependency_setup(required_names: list[str]) -> None:
    global _DEPENDENCY_SETUP
    if _DEPENDENCY_SETUP is not None:
        return
    # Resolve every Unreal-dependent path before starting the worker.  Calls to
    # unreal.Paths are not safe from the background installation thread.
    python_exe = _embedded_python()
    target = activate_vendor_path()
    controller = _DependencySetupController(required_names, python_exe, target)
    _DEPENDENCY_SETUP = controller
    controller.tick_handle = unreal.register_slate_post_tick_callback(
        _dependency_setup_tick
    )


def ensure_runtime_ready(required_names: list[str] | None = None) -> bool:
    if required_names is None:
        missing = missing_runtime_dependencies()
    else:
        missing = missing_runtime_dependencies(required_names)
    if not missing:
        return True

    unreal.log(
        "[ThumbnailCreator] Installing missing runtime dependencies in "
        "Saved/ThumbnailCreator/python_packages: %s" % ", ".join(missing)
    )
    try:
        _start_dependency_setup(missing)
    except Exception as exc:
        _message(
            "critical",
            "Automatic dependency installation failed:\n\n%s" % exc,
        )
        return False
    return False


def _launcher_command() -> str:
    return "import runpy; runpy.run_path(%r, run_name='__main__')" % SCRIPT_PATH


def _replace_entry(
    menus,
    menu_name: str,
    section: str,
    entry_name: str,
    label: str,
    tooltip: str,
    command: str,
) -> None:
    try:
        menus.remove_entry(menu_name, section, entry_name)
    except Exception:
        pass
    menu = menus.extend_menu(menu_name)
    entry = unreal.ToolMenuEntry(
        name=entry_name,
        type=unreal.MultiBlockType.MENU_ENTRY,
    )
    entry.set_label(label)
    entry.set_tool_tip(tooltip)
    entry.set_string_command(
        unreal.ToolMenuStringCommandType.PYTHON,
        "",
        command,
    )
    menu.add_menu_entry(section, entry)


def register_tools_menu() -> bool:
    """Register the Thumbnail Creator entry in UEFN's Tools menu."""
    try:
        menus = unreal.ToolMenus.get()
        try:
            menus.remove_entry(TOOLS_MENU, TOOLS_SECTION, LEGACY_FULL_MENU_ENTRY)
        except Exception:
            pass
        _replace_entry(
            menus,
            TOOLS_MENU,
            TOOLS_SECTION,
            FULL_MENU_ENTRY,
            TOOL_NAME,
            "Open the thumbnail capture, preview, lighting, and batch tool.",
            _launcher_command(),
        )
        menus.refresh_all_widgets()
        return True
    except Exception as exc:
        unreal.log_warning(
            "[ThumbnailCreator] Tools menu registration failed: %s" % exc
        )
        return False


def main():
    if not ensure_runtime_ready(["Pillow"]):
        return None
    _load_pillow_modules()
    register_tools_menu()
    unreal.log("[ThumbnailCreator] Runtime file: %s" % SCRIPT_PATH)
    return open_window()


register_tools_menu()


if __name__ == "__main__":
    main()
