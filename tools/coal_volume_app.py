"""Windowed executable entrypoint, with a frozen-runtime diagnostic mode."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import traceback


def self_test(destination):
    import cv2
    import numpy as np
    import PIL
    import pyrealsense2 as rs
    from live_coal_volume import integrate_labels, Renderer

    result = integrate_labels(np.array([[.0001, .0001], [.0002, .0001]]),
                              np.array([.020, .020]), np.array([1, 2]), .001)
    total = sum(item["volume_cm3"] for item in result.values())
    if abs(total - .020) > 1e-10:
        raise RuntimeError("Metric integration self-test failed")
    renderer = Renderer((0, 0, 430, 257))
    info = {"state": "passed", "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable, "python": sys.version,
            "opencv": cv2.__version__, "numpy": np.__version__, "pillow": PIL.__version__,
            "realsense_module": rs.__file__, "opencv_module": cv2.__file__,
            "font_and_render": list(renderer.template_bgr.shape),
            "known_volume_cm3": total,
            "opencv_gui": [line.strip() for line in cv2.getBuildInformation().splitlines() if "GUI:" in line]}
    Path(destination).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", type=Path)
    diagnostic, _ = parser.parse_known_args()
    try:
        if diagnostic.self_test:
            self_test(diagnostic.self_test)
            return 0
        from live_coal_volume import parse_args, run
        run(parse_args())
        return 0
    except Exception:
        detail = traceback.format_exc()
        error_file = Path(tempfile.gettempdir()) / (
            "CoalVolume_error_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".log")
        error_file.write_text(detail, encoding="utf-8")
        if sys.stderr is not None:
            sys.stderr.write(detail)
        if diagnostic.self_test:
            diagnostic.self_test.write_text(json.dumps({"state": "failed", "error": detail},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "程序启动或运行失败。请关闭 RealSense Viewer 和其他占用相机的程序后重试。\n\n"
                f"详细错误已保存：\n{error_file}", "煤块实时体积测量", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
