"""
Thumbnail Generation and Caching

Generates and caches thumbnails for images, following a similar pattern
to the freedesktop.org thumbnail spec but simplified for our use case.

Cache location: ~/.cache/comfy-viewer/thumbnails/
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger("comfy-viewer.thumbnails")

# Thumbnail settings
THUMBNAIL_SIZE = (256, 256)  # Max dimensions (aspect ratio preserved)
THUMBNAIL_QUALITY = 85  # JPEG/WebP quality
THUMBNAIL_FORMAT = "WEBP"  # Small file size, good quality

# Cache directory (respects XDG_CACHE_HOME on Linux)
_cache_base = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache"))
CACHE_DIR = _cache_base / "comfy-viewer" / "thumbnails"


def get_cache_path(image_path: Path) -> Path:
    """
    Get the cache path for a thumbnail.

    Uses MD5 hash of the absolute path for the filename,
    similar to freedesktop.org spec.
    """
    # Hash the absolute path
    path_str = str(image_path.resolve())
    path_hash = hashlib.md5(path_str.encode()).hexdigest()

    return CACHE_DIR / f"{path_hash}.webp"


def is_thumbnail_valid(image_path: Path, thumb_path: Path) -> bool:
    """
    Check if a cached thumbnail is still valid.

    A thumbnail is valid if:
    1. It exists
    2. It was created after the source image was last modified
    """
    if not thumb_path.exists():
        return False

    try:
        image_mtime = image_path.stat().st_mtime
        thumb_mtime = thumb_path.stat().st_mtime
        return thumb_mtime >= image_mtime
    except OSError:
        return False


def generate_thumbnail(image_path: Path, force: bool = False) -> Optional[Path]:
    """
    Generate a thumbnail for an image.

    Args:
        image_path: Path to the source image
        force: If True, regenerate even if cached thumbnail exists

    Returns:
        Path to the thumbnail, or None if generation failed
    """
    image_path = Path(image_path)

    if not image_path.exists():
        log.warning(f"Source image not found: {image_path}")
        return None

    # Check supported formats
    suffix = image_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        log.debug(f"Unsupported format for thumbnails: {suffix}")
        return None

    thumb_path = get_cache_path(image_path)

    # Check cache validity
    if not force and is_thumbnail_valid(image_path, thumb_path):
        log.debug(f"Using cached thumbnail: {thumb_path.name}")
        return thumb_path

    # Ensure cache directory exists
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (for PNG with transparency)
            if img.mode in ('RGBA', 'P'):
                # Create white background for transparency
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Generate thumbnail (maintains aspect ratio)
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # Save as WebP for good compression
            img.save(thumb_path, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)

        log.debug(f"Generated thumbnail: {thumb_path.name}")
        return thumb_path

    except Exception as e:
        log.error(f"Failed to generate thumbnail for {image_path}: {e}")
        return None


def get_thumbnail(image_path: Path) -> Optional[Path]:
    """
    Get a thumbnail for an image, generating if necessary.

    This is the main entry point for getting thumbnails.
    """
    return generate_thumbnail(image_path, force=False)


def get_thumbnail_for_bytes(filename: str, image_data: bytes) -> Optional[bytes]:
    """
    Generate a thumbnail from image bytes.

    This is used for remote file backends where we don't have a local file path.
    Uses the filename to determine cache path (hashed).

    Args:
        filename: Image filename (used for cache key)
        image_data: Raw image bytes

    Returns:
        Thumbnail image data as bytes, or None if generation failed
    """
    import io

    # Use filename hash for cache path (consistent with local mode)
    cache_key = hashlib.md5(filename.encode()).hexdigest()
    thumb_path = CACHE_DIR / f"{cache_key}.webp"

    # Check if cached thumbnail exists and is still valid
    # For remote mode, we can't check mtime, so just use if exists
    if thumb_path.exists():
        try:
            return thumb_path.read_bytes()
        except OSError:
            pass  # Regenerate if read fails

    # Ensure cache directory exists
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Load image from bytes
        img = Image.open(io.BytesIO(image_data))

        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Generate thumbnail
        img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

        # Save to cache
        img.save(thumb_path, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)

        # Also return the bytes
        output = io.BytesIO()
        img.save(output, format=THUMBNAIL_FORMAT, quality=THUMBNAIL_QUALITY)
        return output.getvalue()

    except Exception as e:
        log.error(f"Failed to generate thumbnail for {filename}: {e}")
        return None


def generate_all_thumbnails(
    source_dir: Path,
    force: bool = False,
    callback: Optional[callable] = None
) -> tuple[int, int]:
    """
    Generate thumbnails for all images in a directory.

    Args:
        source_dir: Directory containing source images
        force: If True, regenerate all thumbnails
        callback: Optional callback(current, total, filename) for progress

    Returns:
        Tuple of (successful, failed) counts
    """
    source_dir = Path(source_dir)

    if not source_dir.exists():
        log.error(f"Source directory not found: {source_dir}")
        return 0, 0

    # Find all images
    extensions = {".png", ".jpg", ".jpeg", ".webp"}
    images = [
        p for p in source_dir.iterdir()
        if p.suffix.lower() in extensions
    ]

    total = len(images)
    successful = 0
    failed = 0

    log.info(f"Generating thumbnails for {total} images...")

    for i, image_path in enumerate(images):
        if callback:
            callback(i + 1, total, image_path.name)

        result = generate_thumbnail(image_path, force=force)

        if result:
            successful += 1
        else:
            failed += 1

    log.info(f"Thumbnail generation complete: {successful} successful, {failed} failed")
    return successful, failed


def clear_cache() -> int:
    """
    Clear all cached thumbnails.

    Returns:
        Number of thumbnails deleted
    """
    if not CACHE_DIR.exists():
        return 0

    count = 0
    for thumb in CACHE_DIR.glob("*.webp"):
        try:
            thumb.unlink()
            count += 1
        except OSError as e:
            log.warning(f"Failed to delete {thumb}: {e}")

    log.info(f"Cleared {count} cached thumbnails")
    return count


def get_cache_stats() -> dict:
    """
    Get statistics about the thumbnail cache.
    """
    if not CACHE_DIR.exists():
        return {"count": 0, "size_bytes": 0, "size_mb": 0}

    thumbnails = list(CACHE_DIR.glob("*.webp"))
    total_size = sum(t.stat().st_size for t in thumbnails)

    return {
        "count": len(thumbnails),
        "size_bytes": total_size,
        "size_mb": round(total_size / (1024 * 1024), 2),
        "cache_dir": str(CACHE_DIR)
    }


def cleanup_orphaned_thumbnails(
    source_dir: Path,
    recursive: bool = True,
    dry_run: bool = False
) -> dict:
    """
    Remove thumbnails that no longer have a corresponding source image.

    Since thumbnails are named by MD5 hash of the source path, we:
    1. Scan source directory for all existing images
    2. Compute expected thumbnail path for each
    3. Remove any cached thumbnails not in the expected set

    Args:
        source_dir: Directory containing source images
        recursive: If True, scan subdirectories too
        dry_run: If True, just report what would be deleted

    Returns:
        Dict with statistics:
        - orphaned: Number of orphaned thumbnails found
        - removed: Number actually removed
        - kept: Number of valid thumbnails retained
        - freed_bytes: Bytes freed by removal
    """
    if not CACHE_DIR.exists():
        return {"orphaned": 0, "removed": 0, "kept": 0, "freed_bytes": 0}

    source_dir = Path(source_dir)
    if not source_dir.exists():
        log.warning(f"Source directory not found: {source_dir}")
        return {"orphaned": 0, "removed": 0, "kept": 0, "freed_bytes": 0}

    # Find all images in source directory
    extensions = {".png", ".jpg", ".jpeg", ".webp"}
    if recursive:
        images = [
            p for p in source_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in extensions
        ]
    else:
        images = [
            p for p in source_dir.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        ]

    # Build set of valid thumbnail filenames
    valid_thumbnails = set()
    for img_path in images:
        thumb_path = get_cache_path(img_path)
        valid_thumbnails.add(thumb_path.name)

    # Scan cached thumbnails and find orphans
    cached_thumbnails = list(CACHE_DIR.glob("*.webp"))
    orphaned = []
    kept = 0

    for thumb in cached_thumbnails:
        if thumb.name in valid_thumbnails:
            kept += 1
        else:
            orphaned.append(thumb)

    # Remove orphans
    removed = 0
    freed_bytes = 0

    for thumb in orphaned:
        try:
            size = thumb.stat().st_size
            if not dry_run:
                thumb.unlink()
            removed += 1
            freed_bytes += size
        except OSError as e:
            log.warning(f"Failed to remove orphaned thumbnail {thumb}: {e}")

    action = "Would remove" if dry_run else "Removed"
    if removed > 0:
        log.info(f"{action} {removed} orphaned thumbnails, freed {freed_bytes / 1024:.1f} KB")

    return {
        "orphaned": len(orphaned),
        "removed": removed,
        "kept": kept,
        "freed_bytes": freed_bytes,
        "source_images": len(images),
    }


# CLI interface for manual thumbnail generation
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    parser = argparse.ArgumentParser(description="Thumbnail cache management")
    parser.add_argument("source_dir", nargs="?", help="Directory containing images")
    parser.add_argument("--force", action="store_true", help="Regenerate all thumbnails")
    parser.add_argument("--clear", action="store_true", help="Clear cache before generating")
    parser.add_argument("--stats", action="store_true", help="Show cache statistics")
    parser.add_argument("--cleanup", action="store_true", help="Remove orphaned thumbnails")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleaned (use with --cleanup)")

    args = parser.parse_args()

    if args.stats:
        stats = get_cache_stats()
        print(f"Cache directory: {stats['cache_dir']}")
        print(f"Thumbnails: {stats['count']}")
        print(f"Total size: {stats['size_mb']} MB")
        sys.exit(0)

    if args.clear:
        cleared = clear_cache()
        print(f"Cleared {cleared} thumbnails")
        if not args.source_dir and not args.cleanup:
            sys.exit(0)

    if args.cleanup:
        if not args.source_dir:
            parser.error("source_dir is required for cleanup (to know which images are valid)")

        result = cleanup_orphaned_thumbnails(
            Path(args.source_dir),
            recursive=True,
            dry_run=args.dry_run
        )

        action = "Would remove" if args.dry_run else "Removed"
        print(f"\nSource images found: {result['source_images']}")
        print(f"Thumbnails kept: {result['kept']}")
        print(f"Orphaned thumbnails: {result['orphaned']}")
        print(f"{action}: {result['removed']}")
        print(f"Space freed: {result['freed_bytes'] / 1024:.1f} KB")
        sys.exit(0)

    if not args.source_dir:
        parser.error("source_dir is required for thumbnail generation")

    def progress(current, total, filename):
        print(f"\r[{current}/{total}] {filename[:40]:<40}", end="", flush=True)

    success, failed = generate_all_thumbnails(
        Path(args.source_dir),
        force=args.force,
        callback=progress
    )

    print(f"\n\nDone: {success} successful, {failed} failed")
