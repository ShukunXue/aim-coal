"""Fit the support plane and integrate coal height from a captured RealSense burst.

Input is capture_rgbd.py's metric NPZ, never a colorized screenshot. Heights and
grid coordinates are in metres. Volumes are upper-surface-to-plane estimates.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from capture_rgbd import write_png


def fit_support_plane(points, reference_height_m, tolerance_m=0.002, seed=42):
    """RANSAC followed by total-least-squares; normal points toward camera."""
    points = np.asarray(points, dtype=np.float64)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 100:
        raise ValueError("Too few valid points to fit the support plane")
    rng = np.random.default_rng(seed)
    fit_points = points[rng.choice(len(points), min(len(points), 30000), replace=False)]
    sample = fit_points[rng.choice(len(fit_points), min(len(fit_points), 6000), replace=False)]
    triangles = fit_points[rng.integers(len(fit_points), size=(500, 3))]
    normals = np.cross(triangles[:, 1] - triangles[:, 0],
                       triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-15)
    normals[normals[:, 2] > 0] *= -1
    offsets = -np.einsum("ij,ij->i", normals, triangles[:, 0])
    accepted = ((lengths > 1e-9) & (-normals[:, 2] > np.cos(np.deg2rad(35)))
                & (np.abs(offsets - reference_height_m) < 0.08))
    normals, offsets = normals[accepted], offsets[accepted]
    if not len(offsets):
        raise ValueError("No support-plane candidate near the supplied camera height")
    best_score, best_plane = -1, None
    for start in range(0, len(offsets), 50):
        n, d = normals[start:start + 50], offsets[start:start + 50]
        scores = (np.abs(sample @ n.T + d) < tolerance_m).sum(axis=0)
        k = int(np.argmax(scores))
        if scores[k] > best_score:
            best_score = int(scores[k])
            best_plane = np.r_[n[k], d[k]]
    for _ in range(4):
        inliers = np.abs(fit_points @ best_plane[:3] + best_plane[3]) < tolerance_m
        if inliers.sum() < 100:
            raise ValueError("Insufficient support-plane consensus")
        ground = fit_points[inliers]
        center = ground.mean(axis=0)
        _, _, vh = np.linalg.svd(ground - center, full_matrices=False)
        normal = vh[-1]
        if normal[2] > 0:
            normal = -normal
        best_plane = np.r_[normal, -center @ normal]
    residuals = points @ best_plane[:3] + best_plane[3]
    inliers = np.abs(residuals) < tolerance_m
    ratio = float(inliers.mean())
    if ratio < 0.35:
        raise ValueError(f"Support plane is unreliable: only {ratio:.1%} inliers")
    if abs(best_plane[3] - reference_height_m) > 0.08:
        raise ValueError("Fitted plane does not agree with supplied camera height")
    return best_plane, {"inlier_fraction": ratio,
                        "inlier_rms_mm": float(np.sqrt(np.mean(residuals[inliers] ** 2)) * 1000),
                        "camera_to_plane_mm": float(best_plane[3] * 1000),
                        "tolerance_mm": tolerance_m * 1000}


def plane_basis(normal):
    normal = np.asarray(normal, dtype=np.float64)
    normal = normal / np.linalg.norm(normal)
    u = np.array([1.0, 0.0, 0.0])
    u -= np.dot(u, normal) * normal
    u /= np.linalg.norm(u)
    return np.stack((u, np.cross(normal, u)), axis=1)


def split_objects(mask, min_pixels=200, core_fraction=0.5):
    """Separate narrow contacts with distance-transform cores and Voronoi growth.

    No object positions or target count are hardcoded. All foreground pixels are
    assigned once; contact splitting is geometric and needs visual verification.
    """
    binary = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                             np.ones((3, 3), np.uint8))
    count, groups, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    result = np.zeros(mask.shape, np.int32)
    records = []
    next_label = 1
    for group in range(1, count):
        if stats[group, cv2.CC_STAT_AREA] < min_pixels:
            continue
        component = (groups == group).astype(np.uint8)
        distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
        cores = (distance > core_fraction * distance.max()).astype(np.uint8)
        nc, core_labels, core_stats, _ = cv2.connectedComponentsWithStats(cores, 8)
        keep = [k for k in range(1, nc) if core_stats[k, 4] >= max(20, min_pixels // 10)]
        if not keep:
            continue
        seed_map = np.isin(core_labels, keep)
        _, nearest = cv2.distanceTransformWithLabels((~seed_map).astype(np.uint8),
                                                    cv2.DIST_L2, 5,
                                                    labelType=cv2.DIST_LABEL_CCOMP)
        pieces = []
        for k in np.unique(nearest[component.astype(bool)]):
            piece = (component > 0) & (nearest == k)
            if piece.sum() >= min_pixels:
                pieces.append(piece)
        for piece in pieces:
            result[piece] = next_label
            records.append({"label": next_label, "source_component": group,
                            "contact_split": len(pieces) > 1})
            next_label += 1
    # Stable reading order: top-to-bottom, using object centroids.
    records.sort(key=lambda r: (np.nonzero(result == r["label"])[0].mean(),
                                np.nonzero(result == r["label"])[1].mean()))
    ordered = np.zeros_like(result)
    for index, record in enumerate(records, 1):
        ordered[result == record["label"]] = index
        record["label"] = index
    return ordered, records


def grid_counts(points, basis, grid_step_m):
    cells, counts = np.unique(np.floor(np.asarray(points) @ basis / grid_step_m).astype(np.int64),
                              axis=0, return_counts=True)
    return {tuple(cell): int(count) for cell, count in zip(cells, counts)}


def integrate_surface(points, heights, basis, grid_step_m=0.001, global_counts=None):
    """Orthographic metric grid; average observed heights, no invented samples.

    Empty cells remain unmeasured and are reported. Counting source image pixels
    as equal area is deliberately avoided, including for a tilted camera.
    """
    if not np.isfinite(grid_step_m) or grid_step_m <= 0:
        raise ValueError("Grid spacing must be positive and finite")
    points, heights = np.asarray(points), np.asarray(heights)
    valid = np.isfinite(points).all(axis=1) & np.isfinite(heights) & (heights > 0)
    if not valid.any():
        raise ValueError("No positive finite heights to integrate")
    uv = points[valid] @ basis
    cells = np.floor(uv / grid_step_m).astype(np.int64)
    unique, inverse = np.unique(cells, axis=0, return_inverse=True)
    sums = np.bincount(inverse, weights=heights[valid])
    counts = np.bincount(inverse)
    cell_heights = sums / counts
    # Shared boundary cells are divided between objects by sample count. The
    # same square millimetre must never be counted twice in the scene volume.
    totals = (counts if global_counts is None else
              np.array([global_counts[tuple(cell)] for cell in unique]))
    areas = grid_step_m ** 2 * counts / totals
    # Diagnostic: holes enclosed by measured grid cells, excluding exterior.
    shifted = unique - unique.min(axis=0) + 1
    shape = tuple((shifted.max(axis=0) + 2).tolist())
    if np.prod(shape) > 5_000_000:
        raise ValueError("Grid is too large; check units or grid spacing")
    occupied = np.zeros(shape, np.uint8)
    occupied[shifted[:, 0], shifted[:, 1]] = 1
    outside = (1 - occupied).copy()
    cv2.floodFill(outside, None, (0, 0), 2)
    holes = int(np.sum(outside == 1))
    return {"volume_cm3": float(np.sum(cell_heights * areas) * 1e6),
            "measured_base_area_cm2": float(areas.sum() * 1e4),
            "observed_grid_cells": len(unique), "enclosed_empty_grid_cells": holes,
            "grid_coverage": float(len(unique) / (len(unique) + holes)),
            "grid_step_mm": grid_step_m * 1000,
            "mean_height_mm": float(np.sum(cell_heights * areas) / areas.sum() * 1000),
            "max_height_mm": float(heights[valid].max() * 1000),
            "hole_policy": "no interpolation; missing cells contribute no volume",
            "shared_cell_policy": "area divided by sample count; no double counting"}


def annotate(color, height, labels, objects, roi, plane_quality, output):
    x, y, w, h = roi
    pad = 8
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(color.shape[1], x + w + pad), min(color.shape[0], y + h + pad)
    crop = color[y0:y1, x0:x1].copy()
    height_crop = height[y0:y1, x0:x1]
    heat = cv2.applyColorMap(np.uint8(np.clip(np.nan_to_num(height_crop) / 0.05, 0, 1) * 255),
                             cv2.COLORMAP_TURBO)
    heat[~np.isfinite(height_crop)] = 0
    colors = [(255, 235, 0), (0, 255, 160), (255, 130, 30), (255, 80, 220), (80, 210, 255)]
    panels = []
    for background in (crop, heat):
        panel = Image.fromarray(cv2.cvtColor(background, cv2.COLOR_BGR2RGB)).resize(
            (crop.shape[1] * 2, crop.shape[0] * 2), Image.Resampling.NEAREST)
        draw = ImageDraw.Draw(panel)
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
        placed = []
        object_rectangles = [((o["bbox_px"][0] - x0) * 2,
                              (o["bbox_px"][1] - y0) * 2,
                              (o["bbox_px"][0] + o["bbox_px"][2] - x0) * 2,
                              (o["bbox_px"][1] + o["bbox_px"][3] - y0) * 2)
                             for o in objects]
        def intersects(a, b):
            return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

        for index, obj in enumerate(objects):
            contour = np.array(obj["contour_px"], np.int32) - [x0, y0]
            coords = [tuple(p) for p in (contour * 2).tolist()]
            rgb = colors[index % len(colors)]
            draw.line(coords + [coords[0]], fill=rgb, width=3)
            bx, by, bw, bh = obj["bbox_px"]
            left, top = (bx - x0) * 2, (by - y0) * 2
            right, bottom = left + bw * 2, top + bh * 2
            draw.rectangle((left, top, right, bottom), outline=rgb, width=1)
            text = f'#{obj["id"]}  V ≈ {obj["volume_cm3"]:.1f} cm³'
            bounds = draw.textbbox((0, 0), text, font=font)
            tw, th = bounds[2] - bounds[0] + 12, 30
            candidates = [(right + 6, top - th - 3), (right - tw, top - th - 3),
                          (right - tw, top - th - 1),
                          (left, top - th - 3), (right + 6, top),
                          (left - tw - 6, top - th - 3)]
            tx, ty = min(max(0, right - tw), panel.width - tw), max(0, top - th - 3)
            for cx, cy in candidates:
                rectangle = (cx, cy, cx + tw, cy + th)
                if (cx >= 0 and cy >= 0 and cx + tw <= panel.width and cy + th <= panel.height
                        and not any(intersects(rectangle, other) for other in object_rectangles + placed)):
                    tx, ty = cx, cy
                    break
            placed.append((tx, ty, tx + tw, ty + th))
            draw.rectangle((tx, ty, tx + tw, ty + th), fill=(12, 20, 28), outline=rgb)
            draw.text((tx + 6, ty + 2), text, font=font, fill=rgb)
        panels.append(panel)
    canvas = Image.new("RGB", (panels[0].width * 2 + 16, panels[0].height + 146), (18, 27, 37))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
    small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    draw.text((14, 8), "RealSense 实拍 · 煤块高度积分（代码实际运行）", font=font, fill="white")
    draw.text((14, 42), "彩色图（已对齐原始深度）", font=small, fill="white")
    draw.text((panels[0].width + 30, 42), "离承载面高度：冷色低 → 暖色高（0–50 mm）", font=small, fill="white")
    canvas.paste(panels[0], (0, 74))
    canvas.paste(panels[1], (panels[0].width + 16, 74))
    base = panels[0].height + 80
    draw.text((14, base), f'承载面距离 {plane_quality["camera_to_plane_mm"]:.1f} mm  |  '
              f'网格 {objects[0]["grid_step_mm"] if objects else 1:g} mm  |  '
              f'目标 {len(objects)} 个  |  体积 = Σ（离地高度 × 网格面积）', font=small, fill="white")
    draw.text((14, base + 30), "上表面至承载面的估算体积；缺失深度未补值；接触处分离需人工核对。", font=small, fill=(255, 205, 100))
    canvas.save(output, format="PNG")


def measure(capture_dir, output_dir, reference_height_m=0.285, roi=None,
            min_height_m=0.005, max_height_m=0.12, min_pixels=200, grid_step_m=0.001):
    capture_dir, output_dir = Path(capture_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("Measurement output directory must be empty")
    data = np.load(capture_dir / "capture.npz", allow_pickle=False)
    depth, rays, color = data["depth_m"], data["rays"], data["color_bgr"]
    points = rays.astype(np.float64) * depth[..., None]
    roi = tuple(roi or (0, 0, depth.shape[1], depth.shape[0]))
    x, y, w, h = roi
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > depth.shape[1] or y + h > depth.shape[0]:
        raise ValueError("ROI must lie inside the native depth image")
    region = np.zeros(depth.shape, bool)
    region[y:y + h, x:x + w] = True
    valid = (region & np.isfinite(depth) & (depth > 0.10)
             & (depth < reference_height_m + 0.10) & (data["temporal_mad_m"] <= 0.002))
    plane, quality = fit_support_plane(points[valid], reference_height_m)
    heights = points @ plane[:3] + plane[3]
    heights[~valid] = np.nan
    foreground = valid & (heights > min_height_m) & (heights < max_height_m)
    labels, groups = split_objects(foreground, min_pixels=min_pixels)
    basis = plane_basis(plane[:3])
    counts_by_step = {step: grid_counts(points[labels > 0], basis, step)
                      for step in set((0.00075, 0.001, 0.00125, grid_step_m))}
    objects = []
    for group in groups:
        index = group["label"]
        mask = labels == index
        metrics = integrate_surface(points[mask], heights[mask], basis, grid_step_m,
                                    counts_by_step[grid_step_m])
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(contour)
        footprint = np.zeros(depth.shape, np.uint8)
        cv2.drawContours(footprint, contours, -1, 1, cv2.FILLED)
        missing = int(np.sum((footprint > 0) & ~valid))
        boundary = (mask[y, x:x + w].any() or mask[y + h - 1, x:x + w].any()
                    or mask[y:y + h, x].any() or mask[y:y + h, x + w - 1].any())
        obj = {"id": index, **metrics, "pixels": int(mask.sum()),
               "bbox_px": [bx, by, bw, bh], "contour_px": contour[:, 0].tolist(),
               "contact_split": group["contact_split"], "touches_roi_edge": bool(boundary),
               "missing_depth_pixels_inside_contour": missing,
               "volume_status": "partial_roi" if boundary else "surface_to_support_estimate",
               "grid_step_sensitivity_cm3": {str(step): integrate_surface(
                   points[mask], heights[mask], basis, step / 1000,
                   counts_by_step[step / 1000])["volume_cm3"]
                   for step in (0.75, 1.0, 1.25)}}
        objects.append(obj)
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "capture_dir": str(capture_dir.resolve()),
        "capture_sha256": hashlib.sha256((capture_dir / "capture.npz").read_bytes()).hexdigest(),
        "source": "live native metric depth captured by capture_rgbd.py",
        "reference_height_m": reference_height_m, "plane_camera_coordinates": plane.tolist(),
        "plane_quality": quality, "roi_xywh": list(roi),
        "parameters": {"minimum_height_mm": min_height_m * 1000,
                       "maximum_height_mm": max_height_m * 1000,
                       "minimum_pixels": min_pixels, "maximum_temporal_mad_mm": 2,
                       "core_fraction": 0.5, "grid_step_mm": grid_step_m * 1000},
        "assumptions": ["stationary scene during capture", "planar support surface",
                        "volume between visible upper surface and support plane",
                        "no interpolation of missing grid cells",
                        "height segmentation does not establish coal/gangue material class"],
        "objects": objects, "total_volume_cm3": sum(o["volume_cm3"] for o in objects),
    }
    (output_dir / "measurements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    np.savez_compressed(output_dir / "geometry.npz", labels=labels, height_m=heights,
                        plane=plane, plane_basis=basis, valid=valid)
    write_png(output_dir / "mask.png", np.uint8(labels > 0) * 255)
    annotate(color, heights, labels, objects, roi, quality, output_dir / "annotated.png")
    summary = {"output": str(output_dir.resolve()), "plane_quality": quality,
               "objects": [{k: v for k, v in o.items() if k != "contour_px"} for o in objects],
               "total_volume_cm3": report["total_volume_cm3"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-height-m", type=float, default=0.285)
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--min-height-mm", type=float, default=5)
    parser.add_argument("--min-pixels", type=int, default=200)
    parser.add_argument("--grid-step-mm", type=float, default=1)
    args = parser.parse_args()
    measure(args.capture_dir, args.output_dir, args.reference_height_m, args.roi,
            min_height_m=args.min_height_mm / 1000, min_pixels=args.min_pixels,
            grid_step_m=args.grid_step_mm / 1000)


if __name__ == "__main__":
    main()
