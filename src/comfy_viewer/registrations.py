"""
Registration System - Unified storage for images and associated data

Each registration represents a generation event with:
- An image (required, what you see in the viewer)
- Associated data (char_str, flags, future extensions via hooks)
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from . import config as app_config
# hooks is a runtime directory at package root
import sys
# Add package root to path for hooks import
_pkg_root = Path(__file__).parent.parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))
from hooks import run_all as run_hooks

log = logging.getLogger("comfy-viewer.registrations")

# Database location (cross-platform data dir)
_config = app_config.load_config()
DB_PATH = Path(_config.get("data_dir", Path.home() / ".local/share/comfy-viewer")) / "registrations.db"

# ─────────────────────────────────────────────────────────────
# Shared Event Processing Logic
# ─────────────────────────────────────────────────────────────

# Tags to prioritize for display (in order of preference)
# If any of these are present, select that image for registration
PREFERRED_IMAGE_TAGS = ["CharImg", "FinalImage", "Output"]


def select_preferred_image(outputs: list) -> tuple[Optional[dict], Optional[str]]:
    """
    Select the best image from a list of Conduit outputs.

    Filters to image outputs only, then selects based on tag priority:
    CharImg > FinalImage > Output > first available image.

    Args:
        outputs: List of output dicts from Conduit event, each with:
            - file_type: "image", "text", etc.
            - tag_name: The output tag name
            - file_path: Path to the file

    Returns:
        Tuple of (selected_output_dict, tag_name) or (None, None) if no images
    """
    # Filter to image outputs only
    image_outputs = []
    for output in outputs:
        if isinstance(output, dict) and output.get("file_type") == "image":
            image_outputs.append(output)
        elif isinstance(output, str) and output.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            # Handle string paths (legacy format)
            image_outputs.append({"file_path": output, "tag_name": "unknown"})

    if not image_outputs:
        return None, None

    # Select preferred image by tag priority
    for preferred_tag in PREFERRED_IMAGE_TAGS:
        for output in image_outputs:
            tag = output.get("tag_name", "unknown")
            if tag == preferred_tag:
                log.debug(f"Selected preferred tag '{preferred_tag}'")
                return output, tag

    # Fallback to first image
    first = image_outputs[0]
    tag = first.get("tag_name", "unknown")
    log.debug(f"No preferred tag found, using '{tag}'")
    return first, tag


def get_relative_image_path(filepath: Path, output_dir: Path) -> str:
    """
    Calculate relative path from output directory.

    Args:
        filepath: Absolute path to the image file
        output_dir: Base output directory

    Returns:
        Relative path string, or just filename if outside output_dir
    """
    try:
        return str(filepath.relative_to(output_dir))
    except ValueError:
        log.warning(f"Output outside OUTPUT_DIR: {filepath}")
        return filepath.name


class RegistrationStore:
    """
    SQLite-backed storage for registrations.

    Thread-safe singleton - use get_store() to access.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._db_lock = threading.RLock()
        self._init_db()
        self._initialized = True
        log.info(f"RegistrationStore initialized: {DB_PATH}")

    def _get_conn(self) -> sqlite3.Connection:
        """Get a connection for the current thread."""
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize database schema."""
        with self._db_lock:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = self._get_conn()
            try:
                conn.executescript("""
                    -- Registrations table: each row is a generation event
                    CREATE TABLE IF NOT EXISTS registrations (
                        id TEXT PRIMARY KEY,              -- From folder name or auto-generated
                        created_at REAL NOT NULL,         -- Unix timestamp
                        source TEXT NOT NULL,             -- "conduit", "file_watcher", "scan"

                        -- The displayable image (required)
                        image_path TEXT NOT NULL UNIQUE,

                        -- Common metadata (columns for fast queries)
                        flagged INTEGER DEFAULT 0,
                        char_str TEXT,                    -- Character name (from hook)

                        -- Extensible data (JSON for future fields from hooks)
                        data TEXT
                    );

                    -- Index for fast timeline queries
                    CREATE INDEX IF NOT EXISTS idx_reg_created
                        ON registrations(created_at DESC);

                    -- Index for flagged items
                    CREATE INDEX IF NOT EXISTS idx_reg_flagged
                        ON registrations(flagged) WHERE flagged = 1;

                    -- Workflow input overrides: user's saved values for each workflow
                    CREATE TABLE IF NOT EXISTS workflow_inputs (
                        workflow_name TEXT NOT NULL,      -- Conduit workflow name
                        input_key TEXT NOT NULL,          -- Input identifier
                        value TEXT,                       -- JSON-encoded value
                        updated_at REAL NOT NULL,         -- Unix timestamp
                        PRIMARY KEY (workflow_name, input_key)
                    );

                    -- Index for fast workflow lookups
                    CREATE INDEX IF NOT EXISTS idx_workflow_inputs_name
                        ON workflow_inputs(workflow_name);

                    -- Settings table: key-value store for app settings
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at REAL NOT NULL
                    );
                """)
                conn.commit()

                # Migration: Add rating column if it doesn't exist
                # rating: -1 = dislike, 0 = neutral (default), 1 = like
                cursor = conn.execute("PRAGMA table_info(registrations)")
                columns = [row[1] for row in cursor.fetchall()]
                if "rating" not in columns:
                    conn.execute("ALTER TABLE registrations ADD COLUMN rating INTEGER DEFAULT 0")
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_reg_rating
                            ON registrations(rating) WHERE rating != 0
                    """)
                    conn.commit()
                    log.info("Migration: Added 'rating' column to registrations table")
            finally:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # Registration Operations
    # ─────────────────────────────────────────────────────────────

    def register(
        self,
        image_path: str,
        source: str = "unknown",
        folder_path: Optional[Path] = None,
        registration_id: Optional[str] = None,
        caller_context: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Register an image with associated data extracted by hooks.

        Args:
            image_path: Relative path to the image (from output dir)
            source: How it was detected ("conduit", "file_watcher", "scan")
            folder_path: Absolute path to folder containing the image
            registration_id: Optional ID (defaults to folder name or generated)
            caller_context: Context from the caller (e.g., generation_type)

        Returns:
            The created/updated registration dict, or None if already exists
        """
        # Use current time as the unique ordering key.
        # Each registration happens at a slightly different moment (microseconds),
        # so the order is locked in at first registration and never changes.
        created_at = time.time()

        # Determine folder path if not provided
        if folder_path is None:
            folder_path = Path(image_path).parent

        # Determine registration ID
        if registration_id is None:
            if "conduit/" in image_path:
                # Use folder name as ID for conduit images
                registration_id = Path(image_path).parent.name
            else:
                # Generate ID from timestamp + random suffix
                import random
                import string
                suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                registration_id = f"{int(created_at)}_{suffix}"

        # Build base data
        current_data = {
            "image_path": image_path,
            "source": source,
            "caller_context": caller_context or {},
        }

        # Run hooks to extract additional data
        if folder_path.exists():
            current_data = run_hooks(folder_path, current_data)

        # Extract known fields, put rest in data JSON
        char_str = current_data.pop("char_str", None)
        image_path = current_data.pop("image_path")
        source = current_data.pop("source")
        current_data.pop("caller_context", None)  # Don't store in DB, only used by hooks

        # Remaining data goes into JSON blob
        extra_data = json.dumps(current_data) if current_data else None

        with self._db_lock:
            conn = self._get_conn()
            try:
                # Use INSERT OR IGNORE to avoid duplicates
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO registrations
                        (id, created_at, source, image_path, char_str, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (registration_id, created_at, source, image_path, char_str, extra_data))
                conn.commit()

                if cursor.rowcount > 0:
                    log.debug(f"Registered: {image_path} (id={registration_id}, char_str={char_str})")
                    return self.get(registration_id)
                else:
                    log.debug(f"Already registered: {image_path}")
                    return None
            finally:
                conn.close()

    def get(self, registration_id: str) -> Optional[dict]:
        """Get a registration by ID."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM registrations WHERE id = ?", (registration_id,)
                ).fetchone()
                if row:
                    return self._row_to_dict(row)
                return None
            finally:
                conn.close()

    def get_by_image(self, image_path: str) -> Optional[dict]:
        """Get a registration by image path."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM registrations WHERE image_path = ?", (image_path,)
                ).fetchone()
                if row:
                    return self._row_to_dict(row)
                return None
            finally:
                conn.close()

    def get_all(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict], int]:
        """
        Get paginated list of registrations sorted by creation time (newest first).

        Returns:
            (list of registration dicts, total count)
        """
        with self._db_lock:
            conn = self._get_conn()
            try:
                # Get total count
                total = conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]

                # Get paginated results
                rows = conn.execute("""
                    SELECT * FROM registrations
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()

                registrations = [self._row_to_dict(row) for row in rows]
                return registrations, total
            finally:
                conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a database row to a registration dict."""
        reg = {
            "id": row["id"],
            "filename": row["image_path"],  # Alias for frontend compatibility
            "image_path": row["image_path"],
            "created_at": row["created_at"],
            "modified": int(row["created_at"]),  # Alias for frontend
            "source": row["source"],
            "flagged": bool(row["flagged"]),
            "char_str": row["char_str"],
            "rating": row["rating"] if "rating" in row.keys() else 0,
        }

        # Parse extra data if present
        if row["data"]:
            try:
                reg["data"] = json.loads(row["data"])
            except json.JSONDecodeError:
                pass

        return reg

    # ─────────────────────────────────────────────────────────────
    # Flag Operations
    # ─────────────────────────────────────────────────────────────

    def set_flagged(self, image_path: str, flagged: bool) -> bool:
        """Set the flagged status of a registration."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "UPDATE registrations SET flagged = ? WHERE image_path = ?",
                    (1 if flagged else 0, image_path)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def toggle_flag(self, image_path: str) -> Optional[bool]:
        """Toggle flag and return new status."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                # Get current status
                row = conn.execute(
                    "SELECT flagged FROM registrations WHERE image_path = ?",
                    (image_path,)
                ).fetchone()
                if not row:
                    return None

                new_status = not bool(row["flagged"])
                conn.execute(
                    "UPDATE registrations SET flagged = ? WHERE image_path = ?",
                    (1 if new_status else 0, image_path)
                )
                conn.commit()
                return new_status
            finally:
                conn.close()

    def get_flagged(self) -> list[dict]:
        """Get all flagged registrations."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("""
                    SELECT * FROM registrations
                    WHERE flagged = 1
                    ORDER BY created_at DESC
                """).fetchall()
                return [self._row_to_dict(row) for row in rows]
            finally:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # Rating Operations (like/dislike)
    # ─────────────────────────────────────────────────────────────

    def set_rating(self, image_path: str, rating: int) -> bool:
        """Set the rating of a registration (-1=dislike, 0=neutral, 1=like)."""
        # Clamp to valid range
        rating = max(-1, min(1, rating))

        with self._db_lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "UPDATE registrations SET rating = ? WHERE image_path = ?",
                    (rating, image_path)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_rating(self, image_path: str) -> Optional[int]:
        """Get the current rating of a registration."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT rating FROM registrations WHERE image_path = ?",
                    (image_path,)
                ).fetchone()
                return row["rating"] if row else None
            finally:
                conn.close()

    def get_by_rating(self, rating: int) -> list[dict]:
        """Get all registrations with a specific rating."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("""
                    SELECT * FROM registrations
                    WHERE rating = ?
                    ORDER BY created_at DESC
                """, (rating,)).fetchall()
                return [self._row_to_dict(row) for row in rows]
            finally:
                conn.close()

    def get_liked(self) -> list[dict]:
        """Get all liked registrations (rating = 1)."""
        return self.get_by_rating(1)

    def get_disliked(self) -> list[dict]:
        """Get all disliked registrations (rating = -1)."""
        return self.get_by_rating(-1)

    # ─────────────────────────────────────────────────────────────
    # Workflow Input Operations (saved user values)
    # ─────────────────────────────────────────────────────────────

    def get_workflow_inputs(self, workflow_name: str) -> dict:
        """Get all saved input values for a workflow."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT input_key, value FROM workflow_inputs WHERE workflow_name = ?",
                    (workflow_name,)
                ).fetchall()
                result = {}
                for row in rows:
                    try:
                        result[row["input_key"]] = json.loads(row["value"])
                    except (json.JSONDecodeError, TypeError):
                        result[row["input_key"]] = row["value"]
                return result
            finally:
                conn.close()

    def set_workflow_input(self, workflow_name: str, input_key: str, value) -> bool:
        """Save a single input value for a workflow."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                json_value = json.dumps(value)
                conn.execute("""
                    INSERT OR REPLACE INTO workflow_inputs
                        (workflow_name, input_key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (workflow_name, input_key, json_value, time.time()))
                conn.commit()
                return True
            finally:
                conn.close()

    def set_workflow_inputs(self, workflow_name: str, inputs: dict) -> bool:
        """Save multiple input values for a workflow (replaces all existing)."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                now = time.time()
                # Clear existing inputs first - this gives REPLACE semantics
                # so that inputs removed from the dict (set to default) are deleted
                conn.execute(
                    "DELETE FROM workflow_inputs WHERE workflow_name = ?",
                    (workflow_name,)
                )
                # Insert new values
                for key, value in inputs.items():
                    json_value = json.dumps(value)
                    conn.execute("""
                        INSERT INTO workflow_inputs
                            (workflow_name, input_key, value, updated_at)
                        VALUES (?, ?, ?, ?)
                    """, (workflow_name, key, json_value, now))
                conn.commit()
                return True
            finally:
                conn.close()

    def clear_workflow_inputs(self, workflow_name: str) -> bool:
        """Delete all saved inputs for a workflow (reset to defaults)."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "DELETE FROM workflow_inputs WHERE workflow_name = ?",
                    (workflow_name,)
                )
                conn.commit()
                return True
            finally:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # Settings (key-value store)
    # ─────────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value by key."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = ?", (key,)
                ).fetchone()
                return row["value"] if row else None
            finally:
                conn.close()

    def set_setting(self, key: str, value: str) -> bool:
        """Set a setting value."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                now = time.time()
                conn.execute("""
                    INSERT OR REPLACE INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value, now))
                conn.commit()
                return True
            finally:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────

    def is_registered(self, image_path: str) -> bool:
        """Check if an image is already registered."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT 1 FROM registrations WHERE image_path = ?", (image_path,)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def get_stats(self) -> dict:
        """Get registration statistics."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                total = conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]
                flagged = conn.execute(
                    "SELECT COUNT(*) FROM registrations WHERE flagged = 1"
                ).fetchone()[0]
                with_char = conn.execute(
                    "SELECT COUNT(*) FROM registrations WHERE char_str IS NOT NULL"
                ).fetchone()[0]

                return {
                    "total": total,
                    "flagged": flagged,
                    "with_char_str": with_char,
                }
            finally:
                conn.close()

    def delete_by_image(self, image_path: str) -> bool:
        """Delete a registration by image path."""
        with self._db_lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM registrations WHERE image_path = ?",
                    (image_path,)
                )
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    log.info(f"Deleted registration: {image_path}")
                return deleted
            finally:
                conn.close()

    def cleanup_orphaned(self, output_dir: Path, dry_run: bool = False) -> dict:
        """
        Remove registrations where the image file no longer exists.

        Args:
            output_dir: Base output directory for resolving image paths
            dry_run: If True, only report what would be deleted

        Returns:
            Dict with 'deleted' count and list of 'orphaned' paths
        """
        orphaned = []

        with self._db_lock:
            conn = self._get_conn()
            try:
                # Get all registered image paths
                rows = conn.execute("SELECT image_path FROM registrations").fetchall()

                for row in rows:
                    image_path = row["image_path"]
                    full_path = output_dir / image_path

                    if not full_path.exists():
                        orphaned.append(image_path)

                if orphaned and not dry_run:
                    # Delete orphaned registrations
                    placeholders = ",".join("?" * len(orphaned))
                    conn.execute(
                        f"DELETE FROM registrations WHERE image_path IN ({placeholders})",
                        orphaned
                    )
                    conn.commit()
                    log.info(f"Cleaned up {len(orphaned)} orphaned registrations")

                return {
                    "deleted": len(orphaned) if not dry_run else 0,
                    "orphaned": orphaned,
                    "dry_run": dry_run,
                }
            finally:
                conn.close()


# Global instance
_store: Optional[RegistrationStore] = None


def get_store() -> RegistrationStore:
    """Get the global RegistrationStore instance."""
    global _store
    if _store is None:
        _store = RegistrationStore()
    return _store
