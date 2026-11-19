"""Capture a static RGB-D burst from a RealSense camera, retaining metric data."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import warnings

import cv2
import numpy as np


def write_png(path, image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"PNG encoding failed: {path}")
    with Path(path).open("xb") as stream:
        stream.write(encoded.tobytes())


def capture(output_dir, width=640, height=480, fps=15, frames=15, warmup=30):
    import pyrealsense2 as rs

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("Capture output directory must be empty; existing data is preserved.")
    ctx = rs.context()
    devices = list(ctx.query_devices())
    if len(devices) != 1:
        raise RuntimeError(f"Expected exactly one RealSense device, found {len(devices)}")
    device = devices[0]
    pipeline = rs.pipeline(ctx)
    config = rs.config()
    serial = device.get_info(rs.camera_info.serial_number)
    config.enable_device(serial)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    started = False
    burst, timestamps, frame_numbers = [], [], []
    align = rs.align(rs.stream.depth)
    try:
        profile = pipeline.start(config)
        started = True
        scale = profile.get_device().first_depth_sensor().get_depth_scale()
        intr = profile.get_stream(rs.stream.depth).as_video_stream_profile().get_intrinsics()
        for _ in range(warmup):
            pipeline.wait_for_frames(10000)
        for i in range(frames):
            frameset = pipeline.wait_for_frames(10000)
            depth_frame = frameset.get_depth_frame()
            if not depth_frame:
                raise RuntimeError("Missing depth frame")
            burst.append(np.asanyarray(depth_frame.get_data()).copy())
            timestamps.append(float(depth_frame.get_timestamp()))
            frame_numbers.append(int(depth_frame.get_frame_number()))
            if i == frames // 2:
                aligned = align.process(frameset)
                color_frame = aligned.get_color_frame()
                if not color_frame:
                    raise RuntimeError("Missing color frame")
                color = np.asanyarray(color_frame.get_data()).copy()
        if intr.model == rs.distortion.none:
            yy, xx = np.indices((height, width), dtype=np.float32)
            rays = np.stack(((xx - intr.ppx) / intr.fx,
                             (yy - intr.ppy) / intr.fy,
                             np.ones_like(xx)), axis=-1)
        else:
            # SDK deprojection handles the actual calibrated distortion model.
            rays = np.array([rs.rs2_deproject_pixel_to_point(intr, [x, y], 1.0)
                             for y in range(height) for x in range(width)],
                            dtype=np.float32).reshape(height, width, 3)
        metadata = {
            "captured_at": datetime.now().astimezone().isoformat(),
            "camera": device.get_info(rs.camera_info.name), "serial": serial,
            "usb": device.get_info(rs.camera_info.usb_type_descriptor),
            "width": width, "height": height, "fps": fps,
            "depth_scale_m": scale, "burst_frames": frames, "warmup_frames": warmup,
            "intrinsics": {"fx": intr.fx, "fy": intr.fy, "ppx": intr.ppx,
                           "ppy": intr.ppy, "model": str(intr.model),
                           "coeffs": list(intr.coeffs)},
            "frame_numbers": frame_numbers, "timestamps_ms": timestamps,
            "color_frame_number": frame_numbers[frames // 2],
            "color_alignment": "color aligned to native depth coordinates using SDK",
            "depth_aggregation": "per-pixel median of nonzero samples, >=60% valid",
            "assumption": "scene stationary during burst; no synthetic hole filling",
        }
    finally:
        if started:
            pipeline.stop()
    stack = np.stack(burst)
    samples = stack.astype(np.float32) * scale
    samples[stack == 0] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(samples, axis=0)
        temporal_mad = np.nanmedian(np.abs(samples - median), axis=0)
    fraction = np.mean(stack > 0, axis=0).astype(np.float32)
    median[fraction < 0.6] = np.nan
    np.savez_compressed(output_dir / "capture.npz", depth_m=median,
                        rays=rays, color_bgr=color, raw_depth_burst=stack,
                        valid_fraction=fraction, temporal_mad_m=temporal_mad)
    (output_dir / "capture.json").write_text(json.dumps(metadata, ensure_ascii=False,
                                                      indent=2), encoding="utf-8")
    write_png(output_dir / "color_aligned.png", color)
    write_png(output_dir / "depth_z16.png",
              np.rint(np.nan_to_num(median) / scale).astype(np.uint16))
    print(json.dumps({"capture": str(output_dir.resolve()), "metadata": metadata,
                      "valid_depth_fraction": float(np.isfinite(median).mean())},
                     ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--frames", type=int, default=15)
    parser.add_argument("--warmup", type=int, default=30)
    args = parser.parse_args()
    if min(args.width, args.height, args.fps, args.frames) <= 0 or args.warmup < 0:
        parser.error("Dimensions, fps and frames must be positive; warmup must be nonnegative")
    capture(args.output_dir, args.width, args.height, args.fps, args.frames, args.warmup)


if __name__ == "__main__":
    main()
