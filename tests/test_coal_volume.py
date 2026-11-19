"""Analytic checks of physical units, plane orientation and contact separation."""

from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from measure_coal_volume import fit_support_plane, grid_counts, integrate_surface, plane_basis, split_objects


class CoalVolumeTests(unittest.TestCase):
    def test_known_prism_and_duplicate_samples(self):
        # 100 cells, each 1 mm² and 20 mm tall: exactly 2 cm³.
        yy, xx = np.indices((10, 10))
        points = np.c_[xx.ravel() * .001 + .0005, yy.ravel() * .001 + .0005,
                       np.zeros(100)]
        basis = np.array([[1, 0], [0, 1], [0, 0]])
        metrics = integrate_surface(np.repeat(points, 3, axis=0), np.full(300, .020), basis)
        self.assertAlmostEqual(metrics["volume_cm3"], 2.0, places=10)
        self.assertAlmostEqual(metrics["measured_base_area_cm2"], 1.0, places=10)

    def test_shared_cells_do_not_double_count(self):
        basis = np.array([[1, 0], [0, 1], [0, 0]])
        points = np.array([[.0001, .0001, 0], [.0002, .0001, 0]])
        counts = grid_counts(points, basis, .001)
        volumes = [integrate_surface(points[k:k+1], [.020], basis,
                                     global_counts=counts)["volume_cm3"] for k in range(2)]
        self.assertAlmostEqual(sum(volumes), .020, places=10)

    def test_tilted_plane_with_foreground_outliers(self):
        rng = np.random.default_rng(7)
        xy = rng.uniform(-.12, .12, (2500, 2))
        z = .285 + .08 * xy[:, 0] - .04 * xy[:, 1]
        points = np.c_[xy, z + rng.normal(0, .00015, len(z))]
        points[:500, 2] -= .025
        plane, quality = fit_support_plane(points, .285)
        self.assertLess(plane[2], 0)
        self.assertAlmostEqual(plane[3], .285 / np.sqrt(1 + .08**2 + .04**2), delta=.0002)
        self.assertLess(quality["inlier_rms_mm"], .3)
        basis = plane_basis(plane[:3])
        np.testing.assert_allclose(basis.T @ basis, np.eye(2), atol=1e-12)
        np.testing.assert_allclose(plane[:3] @ basis, np.zeros(2), atol=1e-12)
        # On the plane distance=0; a point 10 mm along its camera-facing normal has height=10 mm.
        on_plane = -plane[3] * plane[:3]
        self.assertAlmostEqual((on_plane + .010 * plane[:3]) @ plane[:3] + plane[3], .010)

    def test_narrow_contact_splits_without_losing_pixels(self):
        mask = np.zeros((110, 170), np.uint8)
        cv2.circle(mask, (45, 55), 30, 1, -1)
        cv2.circle(mask, (125, 55), 27, 1, -1)
        cv2.rectangle(mask, (70, 52), (103, 58), 1, -1)
        labels, records = split_objects(mask, min_pixels=100)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["contact_split"] for record in records))
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        np.testing.assert_array_equal(labels > 0, opened > 0)

    def test_missing_samples_are_not_filled(self):
        basis = np.array([[1, 0], [0, 1], [0, 0]])
        points = np.array([[.0005, .0005, 0], [.0015, .0005, 0]])
        metrics = integrate_surface(points, [.010, np.nan], basis)
        self.assertEqual(metrics["observed_grid_cells"], 1)
        self.assertAlmostEqual(metrics["volume_cm3"], .010)
        with self.assertRaises(ValueError):
            integrate_surface(points, [.01, .01], basis, grid_step_m=0)


if __name__ == "__main__":
    unittest.main()
