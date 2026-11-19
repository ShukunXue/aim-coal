# 煤块体积测量

使用 Intel RealSense D435i 的彩色和深度帧拟合承载面、分割目标，并把目标高出承载面的部分按网格积分，输出每块目标的估算体积（cm³）。程序支持实时测量、离线复算和单帧 RGB 采集。

## 快速开始

接好 D435i，关闭占用相机的 RealSense Viewer。在项目根目录的 PowerShell 中执行：

```powershell
python -m pip install -r requirements.txt
python -X utf8 .\tools\live_coal_volume.py
```

实时测量默认使用 640×480、15 fps。按 R 重拟合承载面，B 调整测量区域，S 保存当前结果，Q 或 Esc 退出。使用 `--help` 可查看参数，连接多台相机时可用 `--serial` 指定设备。

## 模块与运行入口

| 功能 | 入口 |
| --- | --- |
| 逐帧测量、轮廓显示与状态输出 | `tools/live_coal_volume.py` |
| 同步采集原始深度、彩色帧及标定参数 | `tools/capture_rgbd.py` |
| 从 RGB-D 记录离线分割并估算体积 | `tools/measure_coal_volume.py` |
| 单帧彩色采集 | `tools/capture_rgb.py` |
| Windows 便携程序入口、构建和打包 | `tools/coal_volume_app.py`、`tools/build_windows.ps1`、`tools/package_release.py` |

单帧彩色采集示例（1280×720、15 fps）：

```powershell
python -X utf8 .\tools\capture_rgb.py --fps 15
```

默认保存到 `dataset/train/image/`，也可用 `--output-dir` 指定位置。离线采集及复算示例见[体积验证记录](docs/volume_validation.md)。程序集成、状态文件和 Windows 构建步骤见[实时调用说明](docs/live_volume_usage.md)。

## 验证

```powershell
python -X utf8 -m unittest discover -s tests -v
```

单元测试覆盖体积积分、平面拟合、接触目标分割和实时帧更新。此前用 D435i 采集的静态场景包含 5 个目标，估算总体积约 224.3 cm³。由于缺少独立的标准体积真值，绝对测量误差仍待标定。当前实现只做几何分割和体积测量。
