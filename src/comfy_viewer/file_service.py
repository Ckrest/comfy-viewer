"""
File Service - Abstraction layer for file operations.

Provides a unified interface for accessing images whether they're
on the local filesystem or a remote server. This ensures consistent
behavior between local and network modes.

Usage:
    from file_service import get_file_service

    service = get_file_service(config)

    # List images
    images = service.list_images()

    # Get image data
    data = service.get_image("filename.png")

    # Stream image (for large files)
    for chunk in service.stream_image("filename.png"):
        yield chunk
"""

import hashlib
import io
import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Callable, Any
from datetime import datetime

import requests

log = logging.getLogger("comfy-viewer.file_service")


# ─────────────────────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ImageInfo:
    """Information about an image file."""
    filename: str
    size: int
    modified: float  # Unix timestamp
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @property
    def modified_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.modified)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "size": self.size,
            "modified": self.modified,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "metadata": self.metadata,
        }


@dataclass
class FileEvent:
    """Represents a file change event."""
    type: str  # "created", "deleted", "modified"
    filename: str
    timestamp: float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────
# Abstract Base Class
# ─────────────────────────────────────────────────────────────

class FileService(ABC):
    """
    Abstract base class for file operations.

    All file access goes through this interface, ensuring consistent
    behavior whether files are local or remote.
    """

    # Supported image extensions
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    @abstractmethod
    def list_images(self, subdirectory: str = "") -> list[ImageInfo]:
        """
        List all images in the output directory.

        Args:
            subdirectory: Optional subdirectory to list

        Returns:
            List of ImageInfo objects, sorted by modified time (newest first)
        """
        pass

    @abstractmethod
    def get_image(self, filename: str) -> Optional[bytes]:
        """
        Get the full content of an image file.

        Args:
            filename: Name of the image file

        Returns:
            Image data as bytes, or None if not found
        """
        pass

    @abstractmethod
    def stream_image(self, filename: str, chunk_size: int = 8192) -> Iterator[bytes]:
        """
        Stream image content in chunks.

        Args:
            filename: Name of the image file
            chunk_size: Size of each chunk in bytes

        Yields:
            Chunks of image data
        """
        pass

    @abstractmethod
    def get_image_info(self, filename: str) -> Optional[ImageInfo]:
        """
        Get metadata for a specific image.

        Args:
            filename: Name of the image file

        Returns:
            ImageInfo object, or None if not found
        """
        pass

    @abstractmethod
    def image_exists(self, filename: str) -> bool:
        """Check if an image file exists."""
        pass

    @abstractmethod
    def get_image_path(self, filename: str) -> Optional[Path]:
        """
        Get local filesystem path for an image.

        For local backend, returns the actual path.
        For remote backend, returns None (use get_image() instead).
        """
        pass

    @abstractmethod
    def copy_image(self, filename: str, destination: Path) -> bool:
        """
        Copy an image to a local destination.

        Args:
            filename: Source image filename
            destination: Local path to copy to

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def watch_changes(
        self,
        on_created: Optional[Callable[[str, ImageInfo], None]] = None,
        on_deleted: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Start watching for file changes.

        Args:
            on_created: Callback when new image appears (filename, info)
            on_deleted: Callback when image is deleted (filename)
        """
        pass

    @abstractmethod
    def stop_watching(self) -> None:
        """Stop watching for file changes."""
        pass

    @abstractmethod
    def get_backend_type(self) -> str:
        """Return the backend type: 'local' or 'remote'."""
        pass

    def get_content_type(self, filename: str) -> str:
        """Get MIME type for a filename."""
        ext = Path(filename).suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

    # ─────────────────────────────────────────────────────────────
    # Template/Workflow Methods
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    def list_templates(self) -> list[str]:
        """
        List available workflow templates.

        Returns:
            List of template names (directory names)
        """
        pass

    @abstractmethod
    def get_template_settings(self, template: str) -> Optional[list[dict]]:
        """
        Get settings for a template.

        Args:
            template: Template name

        Returns:
            List of setting definitions, or None if not found
        """
        pass

    @abstractmethod
    def save_template_settings(self, template: str, settings: list[dict]) -> bool:
        """
        Save settings for a template.

        Args:
            template: Template name
            settings: List of setting definitions

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def get_template_graph(self, template: str) -> Optional[dict]:
        """
        Get the workflow graph for a template.

        Args:
            template: Template name

        Returns:
            Graph dictionary (the "output" section), or None if not found
        """
        pass


# ─────────────────────────────────────────────────────────────
# Local File Service
# ─────────────────────────────────────────────────────────────

class LocalFileService(FileService):
    """
    File service implementation for local filesystem.

    Uses direct file I/O and inotify for change detection.
    """

    def __init__(self, output_dir: Path, templates_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir)
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self._observer = None
        self._watching = False
        self._on_created = None
        self._on_deleted = None

        # Ensure directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"LocalFileService initialized: {self.output_dir}")
        if self.templates_dir:
            log.info(f"Templates directory: {self.templates_dir}")

    def _safe_path(self, filename: str) -> Optional[Path]:
        """Resolve filename and ensure it stays within output_dir."""
        path = (self.output_dir / filename).resolve()
        if not path.is_relative_to(self.output_dir.resolve()):
            log.warning(f"Path traversal attempt blocked: {filename}")
            return None
        return path

    def list_images(self, subdirectory: str = "") -> list[ImageInfo]:
        """List images in the output directory."""
        target_dir = self._safe_path(subdirectory) if subdirectory else self.output_dir

        if target_dir is None or not target_dir.exists():
            return []

        images = []
        for path in target_dir.iterdir():
            if path.is_file() and path.suffix.lower() in self.IMAGE_EXTENSIONS:
                info = self._get_image_info_from_path(path)
                if info:
                    images.append(info)

        # Sort by modified time, newest first
        images.sort(key=lambda x: x.modified, reverse=True)
        return images

    def get_image(self, filename: str) -> Optional[bytes]:
        """Read image file content."""
        path = self._safe_path(filename)
        if path is None or not path.exists() or not path.is_file():
            return None

        try:
            return path.read_bytes()
        except Exception as e:
            log.error(f"Failed to read image {filename}: {e}")
            return None

    def stream_image(self, filename: str, chunk_size: int = 8192) -> Iterator[bytes]:
        """Stream image content in chunks."""
        path = self._safe_path(filename)
        if path is None or not path.exists() or not path.is_file():
            return

        try:
            with open(path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk
        except Exception as e:
            log.error(f"Failed to stream image {filename}: {e}")

    def get_image_info(self, filename: str) -> Optional[ImageInfo]:
        """Get metadata for a specific image."""
        path = self._safe_path(filename)
        if path is None or not path.exists() or not path.is_file():
            return None

        return self._get_image_info_from_path(path)

    def image_exists(self, filename: str) -> bool:
        """Check if an image exists."""
        path = self._safe_path(filename)
        return path is not None and path.exists() and path.is_file()

    def get_image_path(self, filename: str) -> Optional[Path]:
        """Get local path for an image."""
        path = self._safe_path(filename)
        if path is not None and path.exists():
            return path
        return None

    def copy_image(self, filename: str, destination: Path) -> bool:
        """Copy an image to a local destination."""
        import shutil

        source = self._safe_path(filename)
        if source is None:
            log.warning(f"Blocked copy of invalid path: {filename}")
            return False

        if not source.exists():
            log.warning(f"Source image not found: {filename}")
            return False

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            log.info(f"Copied {filename} to {destination}")
            return True
        except Exception as e:
            log.error(f"Failed to copy {filename}: {e}")
            return False

    def watch_changes(
        self,
        on_created: Optional[Callable[[str, ImageInfo], None]] = None,
        on_deleted: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Start watching for file changes using inotify."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent, FileDeletedEvent

        self._on_created = on_created
        self._on_deleted = on_deleted

        class Handler(FileSystemEventHandler):
            def __init__(inner_self):
                super().__init__()
                inner_self._pending = {}
                inner_self._lock = threading.Lock()

            def _schedule_process(inner_self, filepath: Path):
                """Debounce file processing."""
                path_str = str(filepath)

                with inner_self._lock:
                    if path_str in inner_self._pending:
                        inner_self._pending[path_str].cancel()

                    timer = threading.Timer(0.5, inner_self._process_file, args=[filepath])
                    inner_self._pending[path_str] = timer
                    timer.start()

            def _process_file(inner_self, filepath: Path):
                """Process a new file after debounce."""
                try:
                    if not filepath.exists():
                        return

                    # Wait for file to be fully written
                    initial_size = filepath.stat().st_size
                    time.sleep(0.2)

                    for _ in range(10):
                        current_size = filepath.stat().st_size
                        if current_size == initial_size and current_size > 0:
                            break
                        initial_size = current_size
                        time.sleep(0.2)

                    info = self._get_image_info_from_path(filepath)
                    if info and self._on_created:
                        self._on_created(filepath.name, info)

                except Exception as e:
                    log.error(f"Error processing new file {filepath}: {e}")
                finally:
                    with inner_self._lock:
                        inner_self._pending.pop(str(filepath), None)

            def on_created(inner_self, event):
                if event.is_directory:
                    return
                filepath = Path(event.src_path)
                if filepath.suffix.lower() in self.IMAGE_EXTENSIONS:
                    inner_self._schedule_process(filepath)

            def on_moved(inner_self, event):
                if event.is_directory:
                    return
                filepath = Path(event.dest_path)
                if filepath.suffix.lower() in self.IMAGE_EXTENSIONS:
                    inner_self._schedule_process(filepath)

            def on_deleted(inner_self, event):
                if event.is_directory:
                    return
                filepath = Path(event.src_path)
                if filepath.suffix.lower() in self.IMAGE_EXTENSIONS:
                    if self._on_deleted:
                        self._on_deleted(filepath.name)

        handler = Handler()
        self._observer = Observer()
        self._observer.schedule(handler, str(self.output_dir), recursive=False)
        self._observer.start()
        self._watching = True

        log.info(f"Started watching (inotify): {self.output_dir}")

    def stop_watching(self) -> None:
        """Stop watching for changes."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._watching = False
        log.info("Stopped watching")

    def get_backend_type(self) -> str:
        return "local"

    def _get_image_info_from_path(self, path: Path) -> Optional[ImageInfo]:
        """Extract image info from a local file."""
        try:
            stat = path.stat()

            info = ImageInfo(
                filename=path.name,
                size=stat.st_size,
                modified=stat.st_mtime,
            )

            # Try to get image dimensions and metadata
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                try:
                    from PIL import Image

                    with Image.open(path) as img:
                        info.width = img.width
                        info.height = img.height
                        info.format = img.format

                        # Extract ComfyUI metadata from PNG
                        if hasattr(img, 'text'):
                            metadata = {}
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
                            if 'parameters' in img.text:
                                metadata['parameters'] = img.text['parameters']
                            info.metadata = metadata

                except Exception as e:
                    log.debug(f"Could not extract image metadata from {path.name}: {e}")

            return info

        except Exception as e:
            log.error(f"Failed to get info for {path}: {e}")
            return None

    # ─────────────────────────────────────────────────────────────
    # Template Methods
    # ─────────────────────────────────────────────────────────────

    def list_templates(self) -> list[str]:
        """List available workflow templates."""
        if not self.templates_dir or not self.templates_dir.exists():
            return []
        return sorted([p.name for p in self.templates_dir.iterdir() if p.is_dir()])

    def get_template_settings(self, template: str) -> Optional[list[dict]]:
        """Get settings for a template."""
        if not self.templates_dir:
            return None

        settings_path = self.templates_dir / template / "settings_template.json"
        if not settings_path.exists():
            return None

        try:
            with open(settings_path, "r") as f:
                from collections import OrderedDict
                return json.load(f, object_pairs_hook=OrderedDict)
        except Exception as e:
            log.error(f"Failed to load template settings {template}: {e}")
            return None

    def save_template_settings(self, template: str, settings: list[dict]) -> bool:
        """Save settings for a template."""
        if not self.templates_dir:
            return False

        settings_path = self.templates_dir / template / "settings_template.json"
        try:
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
            return True
        except Exception as e:
            log.error(f"Failed to save template settings {template}: {e}")
            return False

    def get_template_graph(self, template: str) -> Optional[dict]:
        """Get the workflow graph for a template."""
        if not self.templates_dir:
            return None

        graph_path = self.templates_dir / template / "graph_to_prompt.json"
        if not graph_path.exists():
            return None

        try:
            with open(graph_path, "r") as f:
                data = json.load(f)
            return data.get("output", {})
        except Exception as e:
            log.error(f"Failed to load template graph {template}: {e}")
            return None


# ─────────────────────────────────────────────────────────────
# Remote File Service
# ─────────────────────────────────────────────────────────────

class RemoteFileService(FileService):
    """
    File service implementation for remote server access.

    Connects to another comfy-viewer server over HTTP to access files.
    Uses polling for change detection (inotify doesn't work over network).
    """

    def __init__(
        self,
        remote_url: str,
        poll_interval: float = 2.0,
        timeout: float = 30.0,
    ):
        """
        Initialize remote file service.

        Args:
            remote_url: Base URL of the remote comfy-viewer server
            poll_interval: Seconds between polling for changes
            timeout: HTTP request timeout in seconds
        """
        self.remote_url = remote_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout

        self._session = requests.Session()
        self._watching = False
        self._watch_thread = None
        self._stop_event = threading.Event()
        self._on_created = None
        self._on_deleted = None
        self._known_files: dict[str, float] = {}  # filename -> modified time

        log.info(f"RemoteFileService initialized: {self.remote_url}")

    def list_images(self, subdirectory: str = "") -> list[ImageInfo]:
        """List images from remote server."""
        try:
            params = {"subdir": subdirectory} if subdirectory else {}
            response = self._session.get(
                f"{self.remote_url}/api/files/list",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()

            data = response.json()
            images = [
                ImageInfo(
                    filename=item["filename"],
                    size=item["size"],
                    modified=item["modified"],
                    width=item.get("width"),
                    height=item.get("height"),
                    format=item.get("format"),
                    metadata=item.get("metadata", {}),
                )
                for item in data.get("images", [])
            ]

            return images

        except Exception as e:
            log.error(f"Failed to list images from remote: {e}")
            return []

    def get_image(self, filename: str) -> Optional[bytes]:
        """Download image from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/files/image/{filename}",
                timeout=self.timeout,
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            return response.content

        except Exception as e:
            log.error(f"Failed to get image {filename} from remote: {e}")
            return None

    def stream_image(self, filename: str, chunk_size: int = 8192) -> Iterator[bytes]:
        """Stream image from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/files/image/{filename}",
                stream=True,
                timeout=self.timeout,
            )

            if response.status_code == 404:
                return

            response.raise_for_status()

            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk

        except Exception as e:
            log.error(f"Failed to stream image {filename} from remote: {e}")

    def get_image_info(self, filename: str) -> Optional[ImageInfo]:
        """Get image info from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/files/info/{filename}",
                timeout=self.timeout,
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            return ImageInfo(
                filename=data["filename"],
                size=data["size"],
                modified=data["modified"],
                width=data.get("width"),
                height=data.get("height"),
                format=data.get("format"),
                metadata=data.get("metadata", {}),
            )

        except Exception as e:
            log.error(f"Failed to get info for {filename} from remote: {e}")
            return None

    def image_exists(self, filename: str) -> bool:
        """Check if image exists on remote server."""
        try:
            response = self._session.head(
                f"{self.remote_url}/api/files/image/{filename}",
                timeout=self.timeout,
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_image_path(self, filename: str) -> Optional[Path]:
        """Remote images don't have local paths."""
        return None

    def copy_image(self, filename: str, destination: Path) -> bool:
        """Download image and save to local destination."""
        try:
            data = self.get_image(filename)
            if data is None:
                return False

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            log.info(f"Downloaded {filename} to {destination}")
            return True

        except Exception as e:
            log.error(f"Failed to copy {filename} from remote: {e}")
            return False

    def watch_changes(
        self,
        on_created: Optional[Callable[[str, ImageInfo], None]] = None,
        on_deleted: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Start polling for file changes."""
        self._on_created = on_created
        self._on_deleted = on_deleted
        self._stop_event.clear()

        # Get initial file list
        for img in self.list_images():
            self._known_files[img.filename] = img.modified

        def poll_loop():
            while not self._stop_event.is_set():
                try:
                    current_files = {}
                    for img in self.list_images():
                        current_files[img.filename] = img.modified

                        # Check for new or modified files
                        if img.filename not in self._known_files:
                            log.info(f"Remote: new file detected: {img.filename}")
                            if self._on_created:
                                self._on_created(img.filename, img)
                        elif self._known_files[img.filename] < img.modified:
                            log.info(f"Remote: file modified: {img.filename}")
                            if self._on_created:
                                self._on_created(img.filename, img)

                    # Check for deleted files
                    for filename in self._known_files:
                        if filename not in current_files:
                            log.info(f"Remote: file deleted: {filename}")
                            if self._on_deleted:
                                self._on_deleted(filename)

                    self._known_files = current_files

                except Exception as e:
                    log.warning(f"Polling error: {e}")

                self._stop_event.wait(self.poll_interval)

        self._watch_thread = threading.Thread(target=poll_loop, daemon=True)
        self._watch_thread.start()
        self._watching = True

        log.info(f"Started watching (polling every {self.poll_interval}s): {self.remote_url}")

    def stop_watching(self) -> None:
        """Stop polling for changes."""
        self._stop_event.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=5)
            self._watch_thread = None
        self._watching = False
        log.info("Stopped watching")

    def get_backend_type(self) -> str:
        return "remote"

    # ─────────────────────────────────────────────────────────────
    # Template Methods (fetch from remote server)
    # ─────────────────────────────────────────────────────────────

    def list_templates(self) -> list[str]:
        """List templates from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/templates",
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log.error(f"Failed to list templates from remote: {e}")
            return []

    def get_template_settings(self, template: str) -> Optional[list[dict]]:
        """Get template settings from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/templates/{template}/settings",
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log.error(f"Failed to get template settings from remote: {e}")
            return None

    def save_template_settings(self, template: str, settings: list[dict]) -> bool:
        """Save template settings to remote server."""
        try:
            response = self._session.post(
                f"{self.remote_url}/api/templates/{template}/settings",
                json={"settings": settings},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            log.error(f"Failed to save template settings to remote: {e}")
            return False

    def get_template_graph(self, template: str) -> Optional[dict]:
        """Get template graph from remote server."""
        try:
            response = self._session.get(
                f"{self.remote_url}/api/templates/{template}/graph",
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json().get("graph", {})
        except Exception as e:
            log.error(f"Failed to get template graph from remote: {e}")
            return None


# ─────────────────────────────────────────────────────────────
# Factory Function
# ─────────────────────────────────────────────────────────────

_service_instance: Optional[FileService] = None


def get_file_service(config: dict) -> FileService:
    """
    Get or create the file service based on configuration.

    Config options:
        file_backend: "local" or "remote" (default: "local")
        output_dir: Local path to images (for local backend)
        templates_dir: Local path to templates (for local backend)
        remote_url: URL of remote server (for remote backend)
        poll_interval: Polling interval in seconds (for remote backend)

    Returns:
        FileService instance (singleton)
    """
    global _service_instance

    if _service_instance is not None:
        return _service_instance

    backend = config.get("file_backend", "local")

    if backend == "remote":
        remote_url = config.get("remote_url")
        if not remote_url:
            raise ValueError("remote_url is required for remote file backend")

        _service_instance = RemoteFileService(
            remote_url=remote_url,
            poll_interval=config.get("poll_interval", 2.0),
        )
    else:
        output_dir = config.get("output_dir")
        if not output_dir:
            raise ValueError("output_dir is required for local file backend")

        templates_dir = config.get("templates_dir")
        _service_instance = LocalFileService(
            output_dir=Path(output_dir),
            templates_dir=Path(templates_dir) if templates_dir else None,
        )

    return _service_instance


def reset_file_service() -> None:
    """Reset the file service singleton (for testing or reconfiguration)."""
    global _service_instance

    if _service_instance:
        _service_instance.stop_watching()

    _service_instance = None
