"""
Plugin for "scene_gen" generation type.

Used by:
- SillyTavern SceneGen v1 extension
- SillyTavern SceneGen v2 extension

Reads:
- CharStr.txt for char_str (title)
- STMetaDataOut.txt for prompt (data)
"""

from pathlib import Path


def _read_charstr(folder_path: Path) -> str | None:
    """Read CharStr.txt if it exists."""
    charstr_file = folder_path / "CharStr.txt"
    if charstr_file.exists():
        try:
            content = charstr_file.read_text().strip()
            if content:
                return _clean_char_str(content)
        except Exception:
            pass
    return None


def extract(folder_path: Path, current_data: dict) -> dict:
    """
    Extract data for SceneGen generations.

    Args:
        folder_path: Path to folder containing the image
        current_data: Data collected so far

    Returns:
        {"char_str": "...", "prompt": "..."} or partial dict
    """
    result = {}

    # char_str from CharStr.txt
    char_str = _read_charstr(folder_path)
    if char_str:
        result["char_str"] = char_str

    # prompt from STMetaDataOut.txt
    prompt = _read_st_metadata(folder_path)
    if prompt:
        result["prompt"] = prompt
        if not result.get("char_str"):
            inferred = _infer_char_str_from_prompt(prompt)
            if inferred:
                result["char_str"] = inferred

    return result


def _read_st_metadata(folder_path: Path) -> str | None:
    """Read STMetaDataOut.txt (SillyTavern SceneGen output)."""
    metadata_file = folder_path / "STMetaDataOut.txt"

    if metadata_file.exists():
        try:
            content = metadata_file.read_text().strip()
            if content:
                return content
        except Exception:
            pass

    return None


def _clean_char_str(value: str | None) -> str | None:
    """Filter out placeholder error strings from CharStr.txt."""
    if not value:
        return None
    text = value.strip()
    lower = text.lower()
    if lower.startswith("[file not found:") or lower.startswith("file not found:"):
        return None
    return text


def _infer_char_str_from_prompt(prompt: str) -> str | None:
    """Infer a display title from prompt text when CharStr.txt is missing/invalid."""
    if not prompt:
        return None
    tokens = [token.strip() for token in prompt.replace("\n", ",").split(",") if token.strip()]
    if not tokens:
        return None
    if tokens[0].lower().startswith("embedding:") and len(tokens) > 1:
        candidate = tokens[1]
    else:
        candidate = tokens[0]
    return _clean_char_str(candidate)
