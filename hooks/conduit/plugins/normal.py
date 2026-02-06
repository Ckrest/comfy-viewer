"""
Plugin for "normal" generation type.

Used by:
- ComfyUI Frontend generations
- Comfy-Viewer workflow panel generations

Reads:
- CharStr.txt for char_str (title)
- metadata.txt for prompt (data)
"""

from pathlib import Path


def extract(folder_path: Path, current_data: dict) -> dict:
    """
    Extract data for normal generations.

    Args:
        folder_path: Path to folder containing the image
        current_data: Data collected so far

    Returns:
        {"char_str": "...", "prompt": "..."} or partial dict
    """
    result = {}

    # char_str from CharStr.txt
    char_str = _read_file(folder_path / "CharStr.txt")
    if char_str:
        result["char_str"] = char_str

    # prompt from metadata.txt
    prompt = _read_file(folder_path / "metadata.txt")
    if prompt:
        result["prompt"] = prompt

    return result


def _read_file(file_path: Path) -> str | None:
    """Read a text file if it exists."""
    if file_path.exists():
        try:
            content = file_path.read_text().strip()
            if content:
                return content
        except Exception:
            pass
    return None
