"""Real-time numerical parity and frame replacement, without camera access."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from live_coal_volume import FrameProcessor, integrate_labels
from measure_coal_volume import grid_counts, integrate_surface


class LiveVolumeTests(unittest.TestCase):
    def test_vectorized_integral_matches_validated_integral(self):
        rng = np.random.default_rng(18)
        points = rng.uniform([-.01, -.02, .24], [.05, .02, .27], (3000, 3))
        heights = .285 - points[:, 2]
        labels = rng.integers(1, 4, len(points))
        basis = np.array([[1, 0], [0, 1], [0, 0]])
        counts = grid_counts(points, basis, .001)
        fast = integrate_labels(points @ basis, heights, labels, .001)
        for k in (1, 2, 3):
            mask = labels == k
            reference = integrate_surface(points[mask], heights[mask], basis, .001, counts)
            self.assertAlmostEqual(fast[k]["volume_cm3"], reference["volume_cm3"], places=10)
            self.assertAlmostEqual(fast[k]["base_area_cm2"], reference["measured_base_area_cm2"], places=10)

    def test_moving_and_removed_object_does_not_leave_old_contour(self):
        yy, xx = np.indices((120, 160), dtype=np.float64)
        rays = np.stack(((xx-80)/300, (yy-60)/300, np.ones_like(xx)), axis=-1)
        processor = FrameProcessor(rays, (5, 5, 150, 110), min_pixels=50)
        first = np.full((120, 160), .285)
        first[35:60, 30:55] = .260
        processor.calibrate(first)
        result1 = processor.process(first)
        moved = np.full_like(first, .285)
        moved[35:60, 95:120] = .260
        result2 = processor.process(moved)
        self.assertEqual(len(result1["objects"]), 1)
        self.assertEqual(len(result2["objects"]), 1)
        self.assertAlmostEqual(result2["objects"][0]["centroid_px"][0] -
                               result1["objects"][0]["centroid_px"][0], 65)
        self.assertFalse(result2["labels"][30:55, 25:50].any())
        empty = processor.process(np.full_like(first, .285))
        self.assertEqual(empty["objects"], [])
        self.assertEqual(empty["total_volume_cm3"], 0)
        self.assertFalse(empty["labels"].any())

    def test_missing_depth_cannot_reuse_previous_height(self):
        yy, xx = np.indices((120, 160), dtype=np.float64)
        rays = np.stack(((xx-80)/300, (yy-60)/300, np.ones_like(xx)), axis=-1)
        processor = FrameProcessor(rays, (5, 5, 150, 110), min_pixels=50)
        depth = np.full((120, 160), .285)
        processor.calibrate(depth)
        depth[30:60, 30:60] = .260
        self.assertEqual(len(processor.process(depth)["objects"]), 1)
        depth[30:60, 30:60] = np.nan
        self.assertEqual(processor.process(depth)["objects"], [])


if __name__ == "__main__":
    unittest.main()
