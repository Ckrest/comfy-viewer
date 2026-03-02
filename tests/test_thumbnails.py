import tempfile
import unittest
from pathlib import Path

from PIL import Image

from comfy_viewer import thumbnails


class ThumbnailSizingTests(unittest.TestCase):
    def setUp(self):
        self._orig_cache_dir = thumbnails.CACHE_DIR
        self.tempdir = tempfile.TemporaryDirectory()
        thumbnails.CACHE_DIR = Path(self.tempdir.name) / "thumb-cache"

        self.image_dir = Path(self.tempdir.name) / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.image_path = self.image_dir / "test.png"
        Image.new("RGB", (1024, 768), (20, 80, 200)).save(self.image_path)

    def tearDown(self):
        thumbnails.CACHE_DIR = self._orig_cache_dir
        self.tempdir.cleanup()

    def test_size_preset_helpers(self):
        self.assertEqual(thumbnails.normalize_size_preset("small"), "small")
        self.assertEqual(thumbnails.normalize_size_preset("unknown"), "medium")
        self.assertEqual(thumbnails.get_tile_size_px("large"), 250)
        self.assertEqual(thumbnails.get_render_size_px("large"), 500)
        self.assertEqual(thumbnails.get_render_size_px("bad"), 360)

    def test_cache_path_includes_size(self):
        small_path = thumbnails.get_cache_path(self.image_path, 240)
        large_path = thumbnails.get_cache_path(self.image_path, 500)

        self.assertNotEqual(small_path.name, large_path.name)
        self.assertTrue(small_path.name.endswith("_s240.webp"))
        self.assertTrue(large_path.name.endswith("_s500.webp"))

    def test_generates_distinct_cached_thumbnails_per_size(self):
        small = thumbnails.get_thumbnail(self.image_path, max_size_px=240)
        large = thumbnails.get_thumbnail(self.image_path, max_size_px=500)

        self.assertIsNotNone(small)
        self.assertIsNotNone(large)
        self.assertNotEqual(small, large)
        self.assertTrue(small.exists())
        self.assertTrue(large.exists())

        with Image.open(small) as im_small:
            self.assertLessEqual(max(im_small.size), 240)
        with Image.open(large) as im_large:
            self.assertLessEqual(max(im_large.size), 500)

    def test_cache_stats_reports_size_buckets(self):
        thumbnails.get_thumbnail(self.image_path, max_size_px=240)
        thumbnails.get_thumbnail(self.image_path, max_size_px=500)
        stats = thumbnails.get_cache_stats()

        self.assertEqual(stats["count"], 2)
        self.assertIn("240", stats["count_by_size"])
        self.assertIn("500", stats["count_by_size"])

    def test_cleanup_keeps_all_sizes_for_valid_image(self):
        kept_small = thumbnails.get_thumbnail(self.image_path, max_size_px=240)
        kept_large = thumbnails.get_thumbnail(self.image_path, max_size_px=500)
        self.assertTrue(kept_small.exists())
        self.assertTrue(kept_large.exists())

        orphan = thumbnails.CACHE_DIR / "deadbeefdeadbeefdeadbeefdeadbeef_s240.webp"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"junk")

        result = thumbnails.cleanup_orphaned_thumbnails(self.image_dir, recursive=False, dry_run=False)

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["kept"], 2)
        self.assertFalse(orphan.exists())
        self.assertTrue(kept_small.exists())
        self.assertTrue(kept_large.exists())


if __name__ == "__main__":
    unittest.main()
