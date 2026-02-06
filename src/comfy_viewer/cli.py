"""Command-line interface for comfy-viewer."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import config as app_config
from .version import __version__


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Comfy Viewer server and configuration tools",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"comfy-viewer {__version__}",
    )

    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config file (default: platform config dir)",
    )

    parser.add_argument(
        "--print-defaults",
        action="store_true",
        help="Print default configuration as JSON and exit",
    )
    parser.add_argument(
        "--print-config-schema",
        action="store_true",
        help="Print configuration schema as JSON and exit",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration file and exit",
    )
    parser.add_argument(
        "--print-hook-contract",
        action="store_true",
        help="Print hook contract as JSON and exit",
    )
    parser.add_argument(
        "--print-resolved",
        action="store_true",
        help="Print resolved configuration as JSON and exit",
    )
    parser.add_argument(
        "--print-event-catalog",
        action="store_true",
        help="Print event catalog as JSON and exit",
    )
    parser.add_argument(
        "--print-lifecycle",
        action="store_true",
        help="Print lifecycle points as JSON and exit",
    )

    return parser


def _emit_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _handle_introspection(args: argparse.Namespace) -> Optional[int]:
    config_path = Path(args.config).expanduser() if args.config else None

    if args.print_defaults:
        _emit_json(app_config.config_defaults())
        return 0

    if args.print_config_schema:
        _emit_json(app_config.config_schema())
        return 0

    if args.validate_config:
        errors = app_config.validate_config_file(config_path)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0

    if args.print_hook_contract:
        _emit_json({
            "events": [
                {
                    "name": "registration.extract",
                    "args": ["folder_path", "current_data"],
                    "description": "Run metadata extraction hooks during registration",
                }
            ]
        })
        return 0

    if args.print_resolved:
        _emit_json(app_config.load_config(config_path=config_path))
        return 0

    if args.print_event_catalog:
        _emit_json({"catalog": [
            {"event_type": "operation.completed", "lifecycle_point": "artifact.created",
             "data_fields": ["registration_id", "image_path", "source", "metadata"]},
            {"event_type": "artifact.created", "lifecycle_point": "artifact.created",
             "data_fields": ["file_path", "file_type", "registration_id"]},
            {"event_type": "watch.detected", "lifecycle_point": "watch.triggered",
             "data_fields": ["file_path", "watch_type"]},
            {"event_type": "config.resolved", "lifecycle_point": "config.loaded",
             "data_fields": ["config_path", "host", "port"]},
            {"event_type": "error.handled", "lifecycle_point": "error.occurred",
             "data_fields": ["error_type", "message", "context"]},
        ]})
        return 0

    if args.print_lifecycle:
        _emit_json({"points": [
            "startup", "config.loaded", "request.received",
            "artifact.created", "watch.triggered", "error.occurred", "shutdown",
        ]})
        return 0

    return None


def _run_server(config_path: Optional[Path]) -> int:
    if config_path:
        os.environ[f"{app_config.ENV_PREFIX}_CONFIG"] = str(config_path)

    from . import app as comfy_app

    comfy_app.log.info(
        "Starting server on %s:%s",
        comfy_app.CONFIG.get("host"),
        comfy_app.CONFIG.get("port"),
    )

    try:
        comfy_app.socketio.run(
            comfy_app.app,
            host=comfy_app.CONFIG.get("host", "0.0.0.0"),
            port=comfy_app.CONFIG.get("port", 5000),
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        comfy_app.shutdown()
    return 0


def main(args: Optional[list[str]] = None) -> int:
    parser = create_argument_parser()
    parsed_args = parser.parse_args(args)

    result = _handle_introspection(parsed_args)
    if result is not None:
        return result

    config_path = Path(parsed_args.config).expanduser() if parsed_args.config else None
    return _run_server(config_path)


if __name__ == "__main__":
    sys.exit(main())
