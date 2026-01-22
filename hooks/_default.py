"""
Default Hook - Built-in Image Metadata Reader

This is the ONLY built-in hook for comfy-viewer. It provides baseline labeling
for ANY image source, regardless of where the image came from.

Behavior:
- char_str = image filename (e.g., "image_001.png")
- prompt = embedded image metadata (PNG tEXt, WebP XMP, JPEG EXIF)

This hook runs first (underscore sorts early alphabetically). Later hooks
(like conduit/) can override these values with source-specific data.
"""

import json
from pathlib import Path


def extract(folder_path: Path, current_data: dict) -> dict:
    """
    Extract default metadata from the image file.

    Sets char_str to filename and prompt to embedded metadata.

    Args:
        folder_path: Path to folder containing the image
        current_data: Data collected so far (includes image_path)

    Returns:
        {"char_str": "filename", "prompt": "metadata"} or partial dict
    """
    result = {}
    image_path = current_data.get("image_path")

    if not image_path:
        return result

    # Resolve the image file
    image_file = _resolve_image_path(folder_path, image_path)
    if not image_file:
        return result

    # char_str = filename (without extension for cleaner display)
    result["char_str"] = image_file.stem

    # prompt = embedded metadata
    metadata = _read_embedded_metadata(image_file)
    if metadata:
        result["prompt"] = metadata

    return result


def _resolve_image_path(folder_path: Path, image_path: str) -> Path | None:
    """Resolve image_path to an actual file."""
    if Path(image_path).is_absolute():
        candidate = Path(image_path)
    else:
        candidate = folder_path / Path(image_path).name

    return candidate if candidate.exists() else None


def _read_embedded_metadata(image_file: Path) -> str | None:
    """
    Read metadata embedded in the image file.

    Supports:
    - PNG: tEXt chunks (prompt, parameters, workflow, Comment)
    - Other formats: Limited support via PIL

    Args:
        image_file: Path to the image file

    Returns:
        Metadata string if found, else None
    """
    suffix = image_file.suffix.lower()

    # Only process formats that commonly embed metadata
    if suffix not in (".png", ".webp", ".jpg", ".jpeg"):
        return None

    try:
        from PIL import Image

        with Image.open(image_file) as img:
            if not hasattr(img, "info"):
                return None

            # Try common metadata keys used by ComfyUI and other tools
            for key in ["prompt", "parameters", "workflow", "Comment"]:
                if key in img.info:
                    value = img.info[key]
                    if isinstance(value, str) and value.strip():
                        # Try to extract prompt from JSON structure
                        try:
                            data = json.loads(value)
                            if isinstance(data, dict) and "prompt" in data:
                                return data["prompt"]
                        except json.JSONDecodeError:
                            pass
                        # Use raw value
                        return value.strip()

    except ImportError:
        # PIL not available
        pass
    except Exception:
        pass

    return None
