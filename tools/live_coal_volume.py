"""Real-time RealSense coal contours and metric upper-surface volume.

Uses only the newest synchronized RGB-D frame. The support plane is fitted at
startup and stays fixed while coal moves; R recalibrates it. No temporal depth
averaging or persistence is used, so old silhouettes cannot survive a move.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import traceback

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from measure_coal_volume import fit_support_plane, plane_basis, split_objects


ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1])
WINDOW = "RealSense Coal Volume - LIVE"
PALETTE = [(0, 235, 255), (160, 255, 0), (30, 130, 255), (220, 80, 255), (255, 210, 80)]


def save_json(path, content):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_image(path, image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Unable to encode {path}")
    with Path(path).open("wb") as stream:
        stream.write(encoded.tobytes())


def integrate_labels(uv_m, heights_m, labels, grid_step_m):
    """Vectorized equivalent of the validated per-object metric grid integral.

    Shared cells are apportioned by sample count, exactly as in the offline
    integral. No image-pixel area assumption and no interpolation of depth holes.
    """
    if not np.isfinite(grid_step_m) or grid_step_m <= 0:
        raise ValueError("Grid step must be finite and positive")
    uv_m = np.asarray(uv_m, dtype=np.float64)
    heights_m = np.asarray(heights_m, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.int32)
    valid = (np.isfinite(uv_m).all(axis=1) & np.isfinite(heights_m)
             & (heights_m > 0) & (labels > 0))
    if not valid.any():
        return {}
    uv_m, heights_m, labels = uv_m[valid], heights_m[valid], labels[valid]
    cells = np.floor(uv_m / grid_step_m).astype(np.int64)
    cells -= cells.min(axis=0)
    span = int(cells[:, 1].max()) + 1
    _, inverse, counts = np.unique(cells[:, 0] * span + cells[:, 1],
                                    return_inverse=True, return_counts=True)
    area_weights = grid_step_m ** 2 / counts[inverse]
    areas = np.bincount(labels, weights=area_weights)
    volumes = np.bincount(labels, weights=heights_m * area_weights)
    maximums = np.zeros(int(labels.max()) + 1, dtype=np.float64)
    np.maximum.at(maximums, labels, heights_m)
    return {int(k): {"volume_cm3": float(volumes[k] * 1e6),
                     "base_area_cm2": float(areas[k] * 1e4),
                     "mean_height_mm": float(volumes[k] / areas[k] * 1000),
                     "max_height_mm": float(maximums[k] * 1000)}
            for k in np.flatnonzero(areas) if k > 0}


class FrameProcessor:
    def __init__(self, rays, roi, reference_height_m=.285, min_height_m=.005,
                 grid_step_m=.001, min_pixels=200):
        self.rays = np.asarray(rays, dtype=np.float64)
        self.reference_height_m = reference_height_m
        self.min_height_m = min_height_m
        self.grid_step_m = grid_step_m
        self.min_pixels = min_pixels
        self.plane = None
        self.quality = None
        self.set_roi(roi)

    def set_roi(self, roi):
        x, y, w, h = map(int, roi)
        height, width = self.rays.shape[:2]
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise ValueError("ROI must lie inside the native depth image")
        self.roi = (x, y, w, h)
        self.ray_crop = self.rays[y:y+h, x:x+w]
        self.plane = None

    def valid_depth(self, depth):
        return (np.isfinite(depth) & (depth > .10)
                & (depth < self.reference_height_m + .10))

    def calibrate(self, depth_m):
        x, y, w, h = self.roi
        depth = depth_m[y:y+h, x:x+w]
        valid = self.valid_depth(depth)
        points = self.ray_crop[valid] * depth[valid, None]
        self.plane, self.quality = fit_support_plane(points, self.reference_height_m)
        self.height_rays = self.ray_crop @ self.plane[:3]
        self.uv_rays = self.ray_crop @ plane_basis(self.plane[:3])
        return self.quality

    def process(self, depth_m):
        if self.plane is None:
            raise ValueError("Support plane is not calibrated")
        x, y, w, h = self.roi
        depth = depth_m[y:y+h, x:x+w]
        valid = self.valid_depth(depth)
        heights = depth * self.height_rays + self.plane[3]
        heights[~valid] = np.nan
        foreground = valid & (heights > self.min_height_m) & (heights < .12)
        labels, groups = split_objects(foreground, self.min_pixels)
        yy, xx = np.nonzero(labels)
        metrics = integrate_labels(self.uv_rays[yy, xx] * depth[yy, xx, None],
                                   heights[yy, xx], labels[yy, xx], self.grid_step_m)
        objects = []
        for group in groups:
            k = group["label"]
            mask = np.uint8(labels == k)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours or k not in metrics:
                continue
            contour = max(contours, key=cv2.contourArea)
            bx, by, bw, bh = cv2.boundingRect(contour)
            clipped = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
            objects.append({"id": k, **metrics[k], "bbox_px": [bx+x, by+y, bw, bh],
                            "centroid_px": [float(np.nonzero(mask)[1].mean()+x),
                                            float(np.nonzero(mask)[0].mean()+y)],
                            "contour_px": (contour[:, 0] + [x, y]).tolist(),
                            "touches_roi_edge": clipped, "contact_split": group["contact_split"]})
        return {"objects": objects, "height_m": heights, "labels": labels,
                "valid_fraction": float(valid.mean()),
                "total_volume_cm3": sum(o["volume_cm3"] for o in objects)}


class Renderer:
    def __init__(self, roi, scale=1.5):
        self.roi = roi
        self.scale = scale
        self.font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
        self.small_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 17)
        x, y, w, h = roi
        self.width, self.height = int(w*scale), int(h*scale)
        self.template = Image.new("RGB", (self.width*2+12, self.height+146), (18, 27, 37))
        draw = ImageDraw.Draw(self.template)
        draw.text((12, 6), "煤块实时体积 · 轮廓和体积随当前帧更新", font=self.font, fill="white")
        draw.text((12, 35), "彩色实拍", font=self.small_font, fill="white")
        draw.text((self.width+24, 35), "离承载面高度（0–50 mm）", font=self.small_font, fill="white")
        draw.text((12, self.height+103), "R 重拟合承载面   B 选择测量区域   S 保存图像/数据   Q 或 Esc 退出",
                  font=self.small_font, fill="white")
        draw.text((12, self.height+125), "编号按当前帧排列；边界标注 PARTIAL 表示目标未完整进入测量区域。",
                  font=self.small_font, fill=(255, 205, 100))
        self.template_bgr = cv2.cvtColor(np.asarray(self.template), cv2.COLOR_RGB2BGR)

    def render(self, color_bgr, result, plane_mm, fps, frame_number, process_ms):
        x, y, w, h = self.roi
        color = color_bgr[y:y+h, x:x+w]
        heights = result["height_m"]
        heat = cv2.applyColorMap(np.uint8(np.clip(np.nan_to_num(heights) / .05, 0, 1)*255),
                                 cv2.COLORMAP_TURBO)
        heat[~np.isfinite(heights)] = 0
        canvas = self.template_bgr.copy()
        objects = result["objects"]
        for offset, image in ((0, color), (self.width+12, heat)):
            panel = cv2.resize(image, (self.width, self.height), interpolation=cv2.INTER_NEAREST)
            boxes = []
            for obj in objects:
                bx, by, bw, bh = obj["bbox_px"]
                boxes.append((int((bx-x)*self.scale), int((by-y)*self.scale),
                              int((bx+bw-x)*self.scale), int((by+bh-y)*self.scale)))
            placed = []
            for obj, box in zip(objects, boxes):
                color = PALETTE[(obj["id"]-1) % len(PALETTE)]
                contour = np.rint((np.array(obj["contour_px"])-[x, y])*self.scale).astype(np.int32)
                cv2.polylines(panel, [contour], True, color, 2, cv2.LINE_AA)
                left, top, right, bottom = box
                cv2.rectangle(panel, (left, top), (right, bottom), color, 1)
                text = f'#{obj["id"]} {obj["volume_cm3"]:.1f} cm3'
                if obj["touches_roi_edge"]:
                    text += " PARTIAL"
                tw = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .55, 1)[0][0] + 10
                th = 24
                candidates = [(right+4, top-th-2), (right-tw, top-th-1),
                              (left, top-th-2), (right+4, top), (left-tw-4, top-th-2)]
                tx, ty = max(0, min(right-tw, self.width-tw)), max(0, top-th-2)
                for cx, cy in candidates:
                    rect = (cx, cy, cx+tw, cy+th)
                    overlap = lambda other: (rect[0] < other[2] and rect[2] > other[0]
                                             and rect[1] < other[3] and rect[3] > other[1])
                    if (cx >= 0 and cy >= 0 and cx+tw <= self.width and cy+th <= self.height
                            and not any(overlap(other) for other in boxes+placed)):
                        tx, ty = cx, cy
                        break
                placed.append((tx, ty, tx+tw, ty+th))
                cv2.rectangle(panel, (tx, ty), (tx+tw, ty+th), (12, 20, 28), -1)
                cv2.rectangle(panel, (tx, ty), (tx+tw, ty+th), color, 1)
                cv2.putText(panel, text, (tx+5, ty+17), cv2.FONT_HERSHEY_SIMPLEX,
                            .55, color, 1, cv2.LINE_AA)
            cv2.rectangle(panel, (0, 0), (self.width-1, self.height-1), (160, 160, 160), 1)
            canvas[62:62+self.height, offset:offset+self.width] = panel
        status = (f'LIVE {fps:.1f} FPS | frame {frame_number} | {process_ms:.0f} ms | '
                  f'plane {plane_mm:.1f} mm | objects {len(objects)} | '
                  f'total {result["total_volume_cm3"]:.1f} cm3')
        cv2.putText(canvas, status, (12, self.height+88), cv2.FONT_HERSHEY_SIMPLEX,
                    .51, (160, 255, 160), 1, cv2.LINE_AA)
        return canvas


def make_rays(rs, intrinsics):
    intr = intrinsics
    if intr.model == rs.distortion.none or not any(intr.coeffs):
        yy, xx = np.indices((intr.height, intr.width), dtype=np.float64)
        return np.stack(((xx-intr.ppx)/intr.fx, (yy-intr.ppy)/intr.fy, np.ones_like(xx)), axis=-1)
    return np.array([rs.rs2_deproject_pixel_to_point(intr, [x, y], 1.)
                     for y in range(intr.height) for x in range(intr.width)],
                    dtype=np.float64).reshape(intr.height, intr.width, 3)


def run(args):
    import pyrealsense2 as rs

    out = args.output_dir or ROOT / "runs" / "volume_realtime" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Output directory must be empty; existing sessions are preserved")
    save_json(out / "latest_status.json", {"state": "starting"})
    pipeline = None
    started = False
    frames_processed, displayed = 0, 0
    final_state = "stopped"
    try:
        context = rs.context()
        devices = list(context.query_devices())
        if args.serial:
            devices = [d for d in devices if d.get_info(rs.camera_info.serial_number) == args.serial]
        if len(devices) != 1:
            raise RuntimeError(f"Expected one camera, found {len(devices)}; use --serial if necessary")
        device = devices[0]
        pipeline = rs.pipeline(context)
        config = rs.config()
        config.enable_device(device.get_info(rs.camera_info.serial_number))
        config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
        config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)
        # Capacity one discards old frames when processing is slower than capture.
        queue = rs.frame_queue(1)
        profile = pipeline.start(config, queue)
        started = True
        scale = profile.get_device().first_depth_sensor().get_depth_scale()
        intr = profile.get_stream(rs.stream.depth).as_video_stream_profile().get_intrinsics()
        rays = make_rays(rs, intr)
        processor = FrameProcessor(rays, args.roi, args.reference_height_m,
                                   args.min_height_mm/1000, args.grid_step_mm/1000, args.min_pixels)
        renderer = Renderer(processor.roi, args.display_scale)
        align = rs.align(rs.stream.depth)
        metadata = {"started_at": datetime.now().astimezone().isoformat(),
                    "camera": device.get_info(rs.camera_info.name),
                    "serial": device.get_info(rs.camera_info.serial_number),
                    "usb": device.get_info(rs.camera_info.usb_type_descriptor),
                    "width": args.width, "height": args.height, "fps": args.fps,
                    "depth_scale_m": scale,
                    "intrinsics": {"fx": intr.fx, "fy": intr.fy, "ppx": intr.ppx,
                                   "ppy": intr.ppy, "model": str(intr.model), "coeffs": list(intr.coeffs)},
                    "roi_xywh": list(processor.roi), "grid_step_mm": args.grid_step_mm,
                    "min_height_mm": args.min_height_mm,
                    "temporal_depth_filter": "none; newest frame only",
                    "object_numbering": "current-frame reading order, not persistent tracking"}
        save_json(out / "session.json", metadata)
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW, min(1400, renderer.width*2+12), min(850, renderer.height+146))
        waiting = renderer.template_bgr.copy()
        cv2.putText(waiting, "Starting camera / warming up...", (30, 130),
                    cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 230, 255), 2)
        cv2.imshow(WINDOW, waiting)
        cv2.waitKey(1)
        start = last_new_frame = time.monotonic()
        last_status = 0.
        last_display = None
        last_payload = None
        fps = 0.
        save_requested = False
        recalibrate = False
        saw_frame = False
        calibration_error = None
        while True:
            now = time.monotonic()
            if (out / "stop.request").exists():
                break
            if args.max_seconds and now-start >= args.max_seconds:
                break
            frame = queue.poll_for_frame()
            if frame:
                frameset = frame.as_frameset()
                if not frameset:
                    continue
                depth_frame = frameset.get_depth_frame()
                if not depth_frame or not frameset.get_color_frame():
                    continue
                last_new_frame = now
                saw_frame = True
                frames_processed += 1
                if frames_processed <= args.warmup:
                    if cv2.waitKey(1) & 0xff in (27, ord('q'), ord('Q')):
                        break
                    continue
                tick = time.perf_counter()
                depth_z16 = np.asanyarray(depth_frame.get_data()).copy()
                depth_m = depth_z16.astype(np.float64) * scale
                depth_m[depth_z16 == 0] = np.nan
                aligned = align.process(frameset)
                aligned_color = aligned.get_color_frame()
                if not aligned_color:
                    continue
                color = np.asanyarray(aligned_color.get_data()).copy()
                frame_number = int(depth_frame.get_frame_number())
                if processor.plane is None or recalibrate:
                    try:
                        processor.calibrate(depth_m)
                        recalibrate = False
                        calibration_error = None
                        save_json(out / "plane.json", {"plane": processor.plane.tolist(),
                                  "quality": processor.quality, "frame_number": frame_number,
                                  "roi_xywh": list(processor.roi)})
                    except ValueError as exc:
                        calibration_error = str(exc)
                        processor.plane = None
                        # A new frame, rather than an old estimate, is displayed below.
                if processor.plane is None:
                    last_display = cv2.resize(color, (960, 720))
                    cv2.rectangle(last_display, (0, 0), (960, 85), (0, 0, 0), -1)
                    cv2.putText(last_display, "Cannot fit support plane. Expose more ground; press R.",
                                (12, 35), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 120, 255), 2)
                    last_payload = {"state": "calibration_failed", "error": calibration_error,
                                    "frame_number": frame_number, "objects": []}
                else:
                    result = processor.process(depth_m)
                    elapsed = (time.perf_counter()-tick)*1000
                    instant = 1/max(1e-6, now-last_tick) if displayed else 0.
                    fps = instant if not fps else .85*fps + .15*instant
                    last_tick = now
                    displayed += 1
                    last_display = renderer.render(color, result, processor.quality["camera_to_plane_mm"],
                                                   fps, frame_number, elapsed)
                    last_payload = {"state": "running", "updated_at": datetime.now().astimezone().isoformat(),
                                    "frame_number": frame_number, "displayed_frames": displayed,
                                    "fps": fps, "processing_ms": elapsed,
                                    "plane_distance_mm": processor.quality["camera_to_plane_mm"],
                                    "roi_xywh": list(processor.roi),
                                    "objects": [{k: v for k, v in o.items() if k != "contour_px"}
                                                for o in result["objects"]],
                                    "total_volume_cm3": result["total_volume_cm3"]}
                    if save_requested:
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        save_image(out / f"snapshot_{stamp}.png", last_display)
                        save_json(out / f"snapshot_{stamp}.json", {**last_payload, "objects": result["objects"]})
                        np.savez_compressed(out / f"snapshot_{stamp}.npz", depth_m=depth_m,
                                            rays=rays, color_bgr=color, plane=processor.plane,
                                            labels=result["labels"], roi=np.array(processor.roi))
                        save_requested = False
                cv2.imshow(WINDOW, last_display)
                if displayed == 1 or now-last_status >= 2:
                    save_json(out / "latest_status.json", last_payload)
                    save_image(out / "latest_preview.png", last_display)
                    last_status = now
            elif now-last_new_frame > .75:
                # Never show an old coal outline as if it were a fresh measurement.
                blank = renderer.template_bgr.copy()
                cv2.putText(blank, "No fresh RGB-D frame - measurements cleared", (20, 130),
                            cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 160, 255), 2)
                cv2.imshow(WINDOW, blank)
                if now-last_status >= 2:
                    save_json(out / "latest_status.json", {"state": "waiting_for_camera", "objects": [],
                              "seconds_without_frames": now-last_new_frame})
                    last_status = now
                if now-last_new_frame > 15:
                    raise RuntimeError("No fresh synchronized RGB-D frames for 15 seconds. Close RealSense Viewer.")
            key = cv2.waitKey(1) & 0xff
            if key in (27, ord('q'), ord('Q')):
                break
            if key in (ord('s'), ord('S')):
                save_requested = True
            if key in (ord('r'), ord('R')):
                recalibrate = True
            if key in (ord('b'), ord('B')) and saw_frame and frames_processed > args.warmup:
                title = "Select measurement ROI - ENTER confirm, ESC cancel"
                selected = cv2.selectROI(title, color, showCrosshair=True, fromCenter=False)
                cv2.destroyWindow(title)
                if selected[2] > 0 and selected[3] > 0:
                    processor.set_roi(selected)
                    renderer = Renderer(processor.roi, args.display_scale)
                    metadata["roi_xywh"] = list(processor.roi)
                    save_json(out / "session.json", metadata)
                last_new_frame = time.monotonic()
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
        save_json(out / "latest_status.json", {**(last_payload or {}), "state": "stopped",
                  "displayed_frames": displayed, "stopped_at": datetime.now().astimezone().isoformat()})
    except Exception as exc:
        final_state = "error"
        save_json(out / "latest_status.json", {"state": "error", "error": str(exc),
                                              "traceback": traceback.format_exc()})
        raise
    finally:
        if started:
            pipeline.stop()
        cv2.destroyAllWindows()
        print(json.dumps({"state": final_state, "output_dir": str(out),
                          "displayed_frames": displayed}, ensure_ascii=False), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--serial")
    parser.add_argument("--reference-height-m", type=float, default=.285)
    parser.add_argument("--roi", type=int, nargs=4, default=(90, 78, 430, 257),
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--min-height-mm", type=float, default=5)
    parser.add_argument("--min-pixels", type=int, default=200)
    parser.add_argument("--grid-step-mm", type=float, default=1)
    parser.add_argument("--display-scale", type=float, default=1.5)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-seconds", type=float, default=0, help="0: run until window closes")
    args = parser.parse_args()
    for name in ("width", "height", "fps", "reference_height_m", "min_height_mm", "min_pixels",
                 "grid_step_mm", "display_scale"):
        if not np.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"{name} must be positive and finite")
    if args.warmup < 0 or args.max_seconds < 0:
        parser.error("warmup and max-seconds must be nonnegative")
    return args


if __name__ == "__main__":
    run(parse_args())
