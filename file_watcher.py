"""
File Watcher - Detects new images in the output directory.

Uses inotify (via watchdog) to get instant notifications when
new files appear, regardless of what created them.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent, FileDeletedEvent

log = logging.getLogger("comfy-viewer.watcher")

# Image extensions we care about
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def extract_png_metadata(filepath: Path) -> dict:
    """
    Extract ComfyUI workflow metadata from PNG.

    ComfyUI embeds the full workflow and prompt in PNG tEXt chunks.
    """
    try:
        from PIL import Image

        with Image.open(filepath) as img:
            metadata = {}

            # ComfyUI stores data in these keys
            if hasattr(img, 'text'):
                if 'prompt' in img.text:
                    try:
                        metadata['prompt'] = json.loads(img.text['prompt'])
                    except json.JSONDecodeError:
                        metadata['prompt_raw'] = img.text['prompt']

                if 'workflow' in img.text:
                    try:
                        metadata['workflow'] = json.loads(img.text['workflow'])
                    except json.JSONDecodeError:
                        metadata['workflow_raw'] = img.text['workflow']

                # Sometimes stored as 'parameters'
                if 'parameters' in img.text:
                    metadata['parameters'] = img.text['parameters']

            # Basic image info
            metadata['width'] = img.width
            metadata['height'] = img.height
            metadata['format'] = img.format

            return metadata

    except Exception as e:
        log.warning(f"Failed to extract metadata from {filepath}: {e}")
        return {}


class ImageFileHandler(FileSystemEventHandler):
    """
    Handles file system events for new and deleted images.
    """

    def __init__(
        self,
        callback: Callable[[Path, dict], None],
        delete_callback: Optional[Callable[[Path], None]] = None,
        debounce_seconds: float = 0.5
    ):
        super().__init__()
        self.callback = callback
        self.delete_callback = delete_callback
        self.debounce_seconds = debounce_seconds
        self._pending: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _process_file(self, filepath: Path):
        """Process a new image file."""
        if not filepath.exists():
            return

        if filepath.suffix.lower() not in IMAGE_EXTENSIONS:
            return

        # Wait a moment for file to be fully written
        # (ComfyUI writes progressively)
        try:
            initial_size = filepath.stat().st_size
            time.sleep(0.2)

            # Check if still being written
            for _ in range(10):  # Max 2 seconds
                current_size = filepath.stat().st_size
                if current_size == initial_size and current_size > 0:
                    break
                initial_size = current_size
                time.sleep(0.2)

            # Extract metadata
            metadata = {}
            if filepath.suffix.lower() == '.png':
                metadata = extract_png_metadata(filepath)

            # Generate thumbnail in background
            try:
                from thumbnails import generate_thumbnail
                thumb_path = generate_thumbnail(filepath)
                if thumb_path:
                    log.debug(f"Thumbnail generated: {thumb_path.name}")
            except Exception as e:
                log.warning(f"Thumbnail generation failed: {e}")

            # Add file info
            stat = filepath.stat()
            file_info = {
                "filename": filepath.name,
                "path": str(filepath),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "metadata": metadata
            }

            log.info(f"New image detected: {filepath.name}")
            self.callback(filepath, file_info)

        except Exception as e:
            log.error(f"Error processing {filepath}: {e}")
        finally:
            with self._lock:
                self._pending.pop(str(filepath), None)

    def _schedule_processing(self, filepath: Path):
        """Debounce file processing to handle rapid events."""
        path_str = str(filepath)

        with self._lock:
            # Cancel existing timer for this file
            if path_str in self._pending:
                self._pending[path_str].cancel()

            # Schedule new processing
            timer = threading.Timer(
                self.debounce_seconds,
                self._process_file,
                args=[filepath]
            )
            self._pending[path_str] = timer
            timer.start()

    def on_created(self, event):
        """Handle file creation."""
        if event.is_directory:
            return

        filepath = Path(event.src_path)
        if filepath.suffix.lower() in IMAGE_EXTENSIONS:
            log.debug(f"File created event: {filepath}")
            self._schedule_processing(filepath)

    def on_moved(self, event):
        """Handle file moves (some tools create temp then rename)."""
        if event.is_directory:
            return

        filepath = Path(event.dest_path)
        if filepath.suffix.lower() in IMAGE_EXTENSIONS:
            log.debug(f"File moved event: {filepath}")
            self._schedule_processing(filepath)

    def on_deleted(self, event):
        """Handle file deletion."""
        if event.is_directory:
            return

        filepath = Path(event.src_path)
        if filepath.suffix.lower() in IMAGE_EXTENSIONS:
            log.debug(f"File deleted event: {filepath}")
            if self.delete_callback:
                try:
                    self.delete_callback(filepath)
                except Exception as e:
                    log.error(f"Delete callback error for {filepath}: {e}")


class OutputWatcher:
    """
    Watches the ComfyUI output directory for new and deleted images.

    Usage:
        watcher = OutputWatcher("/path/to/output")
        watcher.on_new_image = lambda path, info: print(f"New: {path}")
        watcher.on_deleted_image = lambda path: print(f"Deleted: {path}")
        watcher.start()
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.observer: Optional[Observer] = None
        self._callback: Optional[Callable[[Path, dict], None]] = None
        self._delete_callback: Optional[Callable[[Path], None]] = None

    @property
    def on_new_image(self) -> Optional[Callable[[Path, dict], None]]:
        return self._callback

    @on_new_image.setter
    def on_new_image(self, callback: Callable[[Path, dict], None]):
        self._callback = callback

    @property
    def on_deleted_image(self) -> Optional[Callable[[Path], None]]:
        return self._delete_callback

    @on_deleted_image.setter
    def on_deleted_image(self, callback: Callable[[Path], None]):
        self._delete_callback = callback

    def _handle_new_image(self, filepath: Path, file_info: dict):
        """Internal handler that calls the registered callback."""
        if self._callback:
            try:
                self._callback(filepath, file_info)
            except Exception as e:
                log.error(f"Callback error for {filepath}: {e}")

    def _handle_deleted_image(self, filepath: Path):
        """Internal handler that calls the delete callback."""
        if self._delete_callback:
            try:
                self._delete_callback(filepath)
            except Exception as e:
                log.error(f"Delete callback error for {filepath}: {e}")

    def start(self):
        """Start watching the output directory."""
        if not self.output_dir.exists():
            log.warning(f"Output directory does not exist: {self.output_dir}")
            self.output_dir.mkdir(parents=True, exist_ok=True)

        handler = ImageFileHandler(
            self._handle_new_image,
            delete_callback=self._handle_deleted_image
        )

        self.observer = Observer()
        self.observer.schedule(handler, str(self.output_dir), recursive=False)
        self.observer.start()

        log.info(f"Watching for new images in: {self.output_dir}")

    def stop(self):
        """Stop watching."""
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
            log.info("File watcher stopped")

    def is_running(self) -> bool:
        """Check if watcher is active."""
        return self.observer is not None and self.observer.is_alive()


# Convenience function for simple usage
_watcher: Optional[OutputWatcher] = None


def start_watching(
    output_dir: str | Path,
    callback: Callable[[Path, dict], None],
    delete_callback: Optional[Callable[[Path], None]] = None
) -> OutputWatcher:
    """
    Start watching a directory for new and deleted images.

    Args:
        output_dir: Directory to watch
        callback: Function called with (filepath, file_info) for each new image
        delete_callback: Function called with (filepath) when an image is deleted

    Returns:
        The OutputWatcher instance
    """
    global _watcher

    if _watcher and _watcher.is_running():
        _watcher.stop()

    _watcher = OutputWatcher(output_dir)
    _watcher.on_new_image = callback
    if delete_callback:
        _watcher.on_deleted_image = delete_callback
    _watcher.start()

    return _watcher


def stop_watching():
    """Stop the global watcher."""
    global _watcher
    if _watcher:
        _watcher.stop()
        _watcher = None
