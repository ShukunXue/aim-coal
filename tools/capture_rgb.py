"""使用 RealSense D435i 采集一张彩色图像；每次运行只保存一张 PNG。"""

import argparse
from datetime import datetime
from pathlib import Path
import sys
from uuid import uuid4


# 根据脚本位置定位项目，避免从其他工作目录启动时把图片存错位置。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dataset" / "train" / "image"


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return number


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="保存目录，默认：项目目录/dataset/train/image")
    parser.add_argument("--width", type=positive_int, default=1280,
                        help="彩色图像宽度，默认 1280")
    parser.add_argument("--height", type=positive_int, default=720,
                        help="彩色图像高度，默认 720")
    parser.add_argument("--fps", type=positive_int, default=30,
                        help="彩色流帧率，默认 30")
    parser.add_argument("--warmup-frames", type=nonnegative_int, default=15,
                        help="丢弃启动帧数以等待自动曝光趋于稳定，默认 30；0 表示取首帧")
    parser.add_argument("--timeout-ms", type=positive_int, default=5000,
                        help="每次等待帧的超时毫秒数，默认 5000")
    parser.add_argument("--serial", help="连接多台相机时，指定目标 RealSense 序列号")
    return parser.parse_args()


def capture_rgb(args):
    # 延迟导入：即使依赖未安装，也能查看 --help。
    try:
        import cv2
        import numpy as np
        import pyrealsense2 as rs
    except ImportError as exc:
        raise RuntimeError(
            "无法导入采集依赖。请在运行本脚本的 Python 环境执行：\n"
            f'  "{sys.executable}" -m pip install -r "{PROJECT_ROOT / "requirements.txt"}"\n'
            f"原始错误：{exc}"
        ) from exc

    context = rs.context()
    devices = list(context.query_devices())
    if not devices:
        raise RuntimeError("未检测到 RealSense 相机。请连接 D435i，检查 USB 数据线，"
                           "并用 RealSense Viewer 确认设备可识别。")
    if args.serial:
        selected = next((device for device in devices
                         if device.get_info(rs.camera_info.serial_number) == args.serial), None)
        if selected is None:
            raise RuntimeError(f"未找到序列号为 {args.serial} 的 RealSense 相机。")
    elif len(devices) == 1:
        selected = devices[0]
    else:
        serials = ", ".join(device.get_info(rs.camera_info.serial_number) for device in devices)
        raise RuntimeError(f"检测到多台相机：{serials}。请使用 --serial 指定设备。")

    serial = selected.get_info(rs.camera_info.serial_number)
    print(f"相机：{selected.get_info(rs.camera_info.name)}，序列号：{serial}")
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline = rs.pipeline(context)
    config = rs.config()
    config.enable_device(serial)
    # 只启用彩色流；BGR 与 OpenCV 的编码通道顺序一致，保存后颜色正确。
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)
    started = False
    try:
        pipeline.start(config)
        started = True
        print(f"彩色流：{args.width}×{args.height} @ {args.fps} fps；"
              f"预热 {args.warmup_frames} 帧……")
        for _ in range(args.warmup_frames):
            pipeline.wait_for_frames(args.timeout_ms)

        frames = pipeline.wait_for_frames(args.timeout_ms)
        color_frame = frames.get_color_frame()
        if not color_frame:
            raise RuntimeError("未收到有效彩色帧，请检查相机彩色流配置。")
        captured_at = datetime.now()
        # 拷贝数据，使数组在释放 SDK 帧和停止相机后仍然有效。
        color_image = np.asanyarray(color_frame.get_data()).copy()
    finally:
        if started:
            pipeline.stop()

    # 无损 PNG；时间戳 + 随机后缀，并用独占创建防止覆盖已有样本。
    filename = f"rgb_{captured_at:%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}.png"
    output_path = output_dir / filename
    ok, encoded = cv2.imencode(".png", color_image)
    if not ok:
        raise RuntimeError("PNG 编码失败，未保存图像。")
    try:
        with output_path.open("xb") as image_file:
            try:
                image_file.write(encoded.tobytes())
            except OSError:
                image_file.close()
                output_path.unlink(missing_ok=True)
                raise
    except OSError as exc:
        raise RuntimeError(f"图像写入失败：{output_path}\n{exc}") from exc
    return output_path


def main():
    args = parse_args()
    try:
        output_path = capture_rgb(args)
    except KeyboardInterrupt:
        print("\n采集已取消。", file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        print("请关闭 Viewer 中的相机流或退出 Viewer 后重试；检查 USB 连接及所选分辨率。",
              file=sys.stderr)
        return 1
    print(f"已保存 1 张彩色图像：{output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
