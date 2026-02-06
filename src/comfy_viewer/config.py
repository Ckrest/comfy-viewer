"""Configuration helpers for comfy-viewer."""

import copy
import os
from pathlib import Path
from typing import Optional, Any

import yaml
from platformdirs import user_config_dir, user_data_dir, user_cache_dir

ENV_PREFIX = "COMFY_VIEWER"
PACKAGE_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = Path(user_config_dir("comfy-viewer"))
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
DATA_DIR = Path(user_data_dir("comfy-viewer"))
CACHE_DIR = Path(user_cache_dir("comfy-viewer"))

DEFAULT_DISPLAY = {
    "title": {"field": "char_str", "label": "Character"},
    "data": {"field": "prompt", "label": "Prompt"},
}

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "comfy_host": "http://127.0.0.1:8188",
    "templates_dir": str(PACKAGE_DIR / "workflows"),
    "data_dir": str(DATA_DIR),
    "cache_dir": str(CACHE_DIR),
    "quicksaves_dir": str(DATA_DIR / "quicksaves"),
    "output_dir": str(DATA_DIR / "output"),
    "randomize_seed": True,
    "file_backend": "local",
    "remote_url": None,
    "poll_interval": 2.0,
    "hooks_dir": str(CONFIG_DIR / "hooks"),
    "display": DEFAULT_DISPLAY,
}

PATH_KEYS = {
    "templates_dir",
    "data_dir",
    "cache_dir",
    "quicksaves_dir",
    "output_dir",
    "hooks_dir",
}


def _env(name: str) -> Optional[str]:
    return os.environ.get(f"{ENV_PREFIX}_{name}")


def _config_path_from_env() -> Optional[Path]:
    value = _env("CONFIG") or _env("CONFIG_PATH")
    if value:
        return Path(value).expanduser()
    return None


def resolve_config_path(config_path: Optional[Path] = None) -> Path:
    return config_path or _config_path_from_env() or DEFAULT_CONFIG_PATH


def _load_config_file(path: Path, strict: bool = False) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        if strict:
            raise ValueError(f"Failed to parse config file {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"Config file {path} must be a mapping")
        return {}
    return data


def read_config_file(config_path: Optional[Path] = None, strict: bool = False) -> dict:
    path = resolve_config_path(config_path)
    return _load_config_file(path, strict=strict)


def _deep_merge(base: dict, update: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_defaults() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def _apply_env_overrides(config: dict) -> dict:
    mapping = {
        "HOST": ("host", str),
        "PORT": ("port", int),
        "COMFY_HOST": ("comfy_host", str),
        "TEMPLATES_DIR": ("templates_dir", str),
        "DATA_DIR": ("data_dir", str),
        "CACHE_DIR": ("cache_dir", str),
        "OUTPUT_DIR": ("output_dir", str),
        "QUICKSAVES_DIR": ("quicksaves_dir", str),
        "RANDOMIZE_SEED": ("randomize_seed", "bool"),
        "FILE_BACKEND": ("file_backend", str),
        "REMOTE_URL": ("remote_url", str),
        "POLL_INTERVAL": ("poll_interval", float),
        "HOOKS_DIR": ("hooks_dir", str),
    }

    for env_name, (key, cast) in mapping.items():
        value = _env(env_name)
        if value is None:
            continue
        if cast == "bool":
            config[key] = value.lower() in ("true", "1", "yes", "on")
            continue
        try:
            config[key] = cast(value)
        except Exception:
            continue

    return config


def _resolve_paths(config: dict, base_dir: Path) -> dict:
    for key in PATH_KEYS:
        value = config.get(key)
        if not value:
            continue
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            root = PACKAGE_DIR if key == "templates_dir" else base_dir
            path = root / path
        config[key] = str(path)
    return config


def load_config(
    config_path: Optional[Path] = None,
    overrides: Optional[dict] = None,
    strict: bool = False,
) -> dict:
    """Load configuration with precedence: file -> env -> overrides."""
    resolved_path = resolve_config_path(config_path)
    base = config_defaults()
    file_config = _load_config_file(resolved_path, strict=strict)
    merged = _deep_merge(base, file_config)
    merged = _apply_env_overrides(merged)

    if overrides:
        merged = _deep_merge(merged, overrides)

    output_locked = "output_dir" in file_config or _env("OUTPUT_DIR") is not None
    quicksaves_locked = "quicksaves_dir" in file_config or _env("QUICKSAVES_DIR") is not None
    if overrides:
        output_locked = output_locked or "output_dir" in overrides
        quicksaves_locked = quicksaves_locked or "quicksaves_dir" in overrides

    data_dir = Path(str(merged.get("data_dir", DATA_DIR))).expanduser()
    if not output_locked:
        merged["output_dir"] = str(data_dir / "output")
    if not quicksaves_locked:
        merged["quicksaves_dir"] = str(data_dir / "quicksaves")

    base_dir = resolved_path.parent if resolved_path else PACKAGE_DIR
    merged = _resolve_paths(merged, base_dir)
    return merged


def write_config(config: dict, config_path: Optional[Path] = None) -> Path:
    path = resolve_config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, default_flow_style=False, sort_keys=False))
    return path


def config_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "comfy_host": {"type": "string"},
            "templates_dir": {"type": "string"},
            "data_dir": {"type": "string"},
            "cache_dir": {"type": "string"},
            "quicksaves_dir": {"type": "string"},
            "output_dir": {"type": "string"},
            "randomize_seed": {"type": "boolean"},
            "file_backend": {"type": "string", "enum": ["local", "remote"]},
            "remote_url": {"type": ["string", "null"]},
            "poll_interval": {"type": "number", "minimum": 0},
            "hooks_dir": {"type": ["string", "null"]},
            "display": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["field", "label"],
                        "additionalProperties": False,
                    },
                    "data": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["field", "label"],
                        "additionalProperties": False,
                    },
                },
                "required": ["title", "data"],
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_config_dict(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Config must be a mapping/object"]

    schema = config_schema()
    props = schema.get("properties", {})
    allowed_keys = set(props.keys())

    for key in data.keys():
        if key not in allowed_keys:
            errors.append(f"Unknown config key: {key}")

    def check_type(key: str, value: Any, expected: str) -> None:
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{key} must be a string")
        elif expected == "integer" and not _is_int(value):
            errors.append(f"{key} must be an integer")
        elif expected == "number" and not isinstance(value, (int, float)):
            errors.append(f"{key} must be a number")
        elif expected == "boolean" and not isinstance(value, bool):
            errors.append(f"{key} must be a boolean")
        elif expected == "object" and not isinstance(value, dict):
            errors.append(f"{key} must be an object")

    for key, value in data.items():
        if key not in props:
            continue
        spec = props[key]
        expected = spec.get("type")
        if isinstance(expected, list):
            if value is None and "null" in expected:
                continue
            if "string" in expected and isinstance(value, str):
                continue
            errors.append(f"{key} must be one of types: {', '.join(expected)}")
            continue
        if isinstance(expected, str):
            check_type(key, value, expected)

        if key == "file_backend" and value not in {"local", "remote"}:
            errors.append("file_backend must be 'local' or 'remote'")
        if key == "port" and _is_int(value) and not (1 <= value <= 65535):
            errors.append("port must be between 1 and 65535")
        if key == "poll_interval" and isinstance(value, (int, float)) and value < 0:
            errors.append("poll_interval must be >= 0")
        if key == "display" and isinstance(value, dict):
            for field in ("title", "data"):
                if field not in value:
                    errors.append(f"display.{field} is required")
                elif not isinstance(value[field], dict):
                    errors.append(f"display.{field} must be an object")
                else:
                    for nested in ("field", "label"):
                        if nested not in value[field]:
                            errors.append(f"display.{field}.{nested} is required")

    return errors


def validate_config_file(config_path: Optional[Path] = None) -> list[str]:
    path = resolve_config_path(config_path)
    if not path.exists():
        return []
    data = _load_config_file(path, strict=True)
    return validate_config_dict(data)
