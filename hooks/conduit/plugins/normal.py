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
    char_str = _clean_char_str(_read_file(folder_path / "CharStr.txt"))
    if char_str:
        result["char_str"] = char_str

    # prompt from metadata.txt
    prompt = _read_file(folder_path / "metadata.txt")
    if prompt:
        result["prompt"] = prompt
        if not result.get("char_str"):
            inferred = _infer_char_str_from_prompt(prompt)
            if inferred:
                result["char_str"] = inferred

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
    """
    Infer a display title from prompt text when CharStr.txt is missing/invalid.

    Uses the first likely subject token, typically right after embedding tags.
    """
    if not prompt:
        return None
    tokens = [token.strip() for token in prompt.replace("\n", ",").split(",") if token.strip()]
    if not tokens:
        return None

    if tokens[0].lower().startswith("embedding:") and len(tokens) > 1:
        candidate = tokens[1]
    else:
        candidate = tokens[0]

    cleaned = _clean_char_str(candidate)
    return cleaned
