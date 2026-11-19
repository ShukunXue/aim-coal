# 煤块实时体积测量调用说明

## 便携程序

解压完整便携包，双击 `CoalVolume.exe`。程序使用最新同步 RGB-D 帧，启动时拟合承载面，
随后逐帧分割并积分；不会累积移动前的轮廓。请先关闭 RealSense Viewer。

快捷键：R 重拟合承载面；B 调整测量区域；S 保存当前图像及深度数据；Q / Esc 退出。

默认参数是已经实机验证的 640×480、15 fps、参考高度 285 mm、1 mm 网格、
5 mm 前景高度阈值和测量区域 `(90, 78, 430, 257)`。区域外不计入测量。
编号按当前帧排列，不是跨帧的永久目标编号。

## 从其他程序启动

PowerShell 示例（在 exe 所在文件夹）：

```powershell
& '.\CoalVolume.exe' --reference-height-m 0.285 --grid-step-mm 1 --min-height-mm 5 --roi 90 78 430 257
```

可指定输出目录，读取其中 `latest_status.json` 获取最新体积数据：

```powershell
& '.\CoalVolume.exe' --output-dir 'C:\coal-results\run-001'
```

输出目录须为空；每次使用新目录，避免覆盖历史测量。该 exe 是窗口程序，
请使用 JSON 文件而非标准输出获取状态。状态约每 2 秒写入一次，画面按相机帧率实时更新。
`state=running` 表示测量中，`waiting_for_camera` 表示没有新帧，`stopped` 表示已经退出。
读取 JSON 时应检查 `updated_at`；退出后的数值是最后一帧记录，不是仍在进行的测量。

Python 启动示例：

```python
import subprocess

process = subprocess.Popen([
    r'C:\your-path\CoalVolume\CoalVolume.exe',
    '--output-dir', r'C:\coal-results\run-002',
    '--reference-height-m', '0.285',
])
```

程序启动后，也可在当前输出目录创建 `stop.request` 空文件，令程序正常退出并释放相机。

## 直接运行源码

在源码项目根目录执行：

```powershell
python -X utf8 .\tools\live_coal_volume.py
```

源程序支持 `--help`、`--width`、`--height`、`--fps`、`--serial`、`--reference-height-m`、
`--roi`、`--min-height-mm`、`--min-pixels`、`--grid-step-mm`、`--display-scale`、
`--warmup`、`--output-dir` 和用于限时验证的 `--max-seconds`。

无需窗口的集成层是 `tools/live_coal_volume.py` 中的 `FrameProcessor`：
输入同一相机标定的单位深度射线 `rays[H,W,3]` 与米制深度 `depth_m[H,W]`，
先调用 `calibrate(depth_m)`，再逐帧调用 `process(depth_m)`。
其返回 `objects`（体积、轮廓、边框）、`height_m`、`labels`、`total_volume_cm3`。

## 重新构建便携程序

在源码项目根目录执行，并将 `-Python` 指向装有项目依赖及 `Library/bin` 图像 DLL 的 Conda 环境：

```powershell
& .\tools\build_windows.ps1 -Python (Get-Command python).Source
```

构建工具放在项目 `.build/packaging-deps`，不安装到原有 deeplearning 环境。
再次构建前请退出便携程序，以免可执行文件被占用。产物在 `release/CoalVolume/`。

构建后可在不打开相机的情况下检查封装的依赖、字体和积分单位：

```powershell
Start-Process '.\CoalVolume.exe' -ArgumentList @('--self-test','C:\coal-results\self-test.json') -Wait
```

首次测量的离线验证过程见源码项目中的 `docs/volume_validation.md`。
