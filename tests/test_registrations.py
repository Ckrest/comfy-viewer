import tempfile
import unittest
from pathlib import Path

from comfy_viewer import registrations


class RegistrationPaginationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = registrations.DB_PATH
        self.original_store = registrations._store
        self.original_instance = registrations.RegistrationStore._instance

        registrations.DB_PATH = Path(self.tempdir.name) / "registrations.db"
        registrations._store = None
        registrations.RegistrationStore._instance = None
        self.store = registrations.RegistrationStore()

    def tearDown(self):
        registrations.DB_PATH = self.original_db_path
        registrations._store = self.original_store
        registrations.RegistrationStore._instance = self.original_instance
        self.tempdir.cleanup()

    def insert_registration(self, registration_id, created_at, image_path):
        with self.store._db_lock:
            conn = self.store._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO registrations
                        (id, created_at, source, image_path, flagged, char_str, data, rating)
                    VALUES (?, ?, ?, ?, 0, NULL, NULL, 0)
                    """,
                    (registration_id, created_at, "test", image_path),
                )
                conn.commit()
            finally:
                conn.close()

    def seed_sample_rows(self):
        self.insert_registration("a", 1000.0, "conduit/job-a/a.png")
        self.insert_registration("b", 1000.0, "conduit/job-b/b.png")
        self.insert_registration("c", 999.0, "c.png")
        self.insert_registration("d", 998.0, "d.png")
        self.insert_registration("e", 997.0, "e.png")

    def test_get_all_uses_deterministic_tiebreaker(self):
        self.seed_sample_rows()

        registrations_page, total = self.store.get_all(0, 5)

        self.assertEqual(total, 5)
        self.assertEqual(
            [row["filename"] for row in registrations_page],
            [
                "conduit/job-b/b.png",
                "conduit/job-a/a.png",
                "c.png",
                "d.png",
                "e.png",
            ],
        )

    def test_get_page_supports_stable_older_and_newer_navigation(self):
        self.seed_sample_rows()

        first_page, total, first_info = self.store.get_page(limit=2)
        self.assertEqual(total, 5)
        self.assertEqual(
            [row["filename"] for row in first_page],
            ["conduit/job-b/b.png", "conduit/job-a/a.png"],
        )
        self.assertTrue(first_info["has_older"])
        self.assertFalse(first_info["has_newer"])
        self.assertEqual(first_info["older_cursor"], (1000.0, "a"))

        second_page, _, second_info = self.store.get_page(
            limit=2,
            cursor=first_info["older_cursor"],
            direction="older",
        )
        self.assertEqual(
            [row["filename"] for row in second_page],
            ["c.png", "d.png"],
        )
        self.assertTrue(second_info["has_newer"])
        self.assertTrue(second_info["has_older"])
        self.assertEqual(second_info["newer_cursor"], (999.0, "c"))

        newer_page, _, newer_info = self.store.get_page(
            limit=2,
            cursor=second_info["newer_cursor"],
            direction="newer",
        )
        self.assertEqual(
            [row["filename"] for row in newer_page],
            ["conduit/job-b/b.png", "conduit/job-a/a.png"],
        )
        self.assertFalse(newer_info["has_newer"])
        self.assertTrue(newer_info["has_older"])

    def test_find_image_position_handles_nested_paths(self):
        self.seed_sample_rows()

        result = self.store.find_image_position("conduit/job-a/a.png")

        self.assertIsNotNone(result)
        self.assertEqual(result["registration"]["filename"], "conduit/job-a/a.png")
        self.assertEqual(result["index"], 1)
        self.assertEqual(result["total"], 5)

    def test_get_window_for_image_returns_contiguous_neighbors(self):
        self.seed_sample_rows()

        result = self.store.get_window_for_image("c.png", before=1, after=1)

        self.assertIsNotNone(result)
        self.assertEqual(
            [row["filename"] for row in result["registrations"]],
            ["conduit/job-a/a.png", "c.png", "d.png"],
        )
        self.assertEqual(result["start_index"], 1)
        self.assertEqual(result["current_index"], 1)
        self.assertTrue(result["page"]["has_newer"])
        self.assertTrue(result["page"]["has_older"])


if __name__ == "__main__":
    unittest.main()
