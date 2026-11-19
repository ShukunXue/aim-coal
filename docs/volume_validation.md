# 实时 RGB-D 高度积分验证

程序直接读取 RealSense D435i 的原始 Z16 深度和相机内参，不从截图颜色反推体积。
本次相机连接为 USB 2.1，实际启用深度和彩色 640×480、15 fps。
运行前应退出 RealSense Viewer，以释放相机。采集完成后程序自动停止相机流。

现已实现并实机验证实时版 `tools/live_coal_volume.py`，约 15 fps。
移动煤块后逐帧更新，无静态多帧平均残影。用户已现场测试确认符合要求。
便携版和源码调用方式见 [实时调用说明](live_volume_usage.md)。下文保留首次离线验证记录。

## 重新采集并计算

在项目根目录使用已安装项目依赖的 Python 环境运行以下命令。每次创建新目录，保留历史数据。

```powershell
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$runRoot = Join-Path '.\runs\volume_live' $runStamp
python -X utf8 .\tools\capture_rgbd.py --output-dir "$runRoot\capture" --frames 15 --warmup 30
if ($LASTEXITCODE -ne 0) { throw 'RGB-D capture failed' }
python -X utf8 .\tools\measure_coal_volume.py --capture-dir "$runRoot\capture" --output-dir "$runRoot\measurement" --reference-height-m 0.285 --roi 90 78 430 257 --grid-step-mm 1 --min-height-mm 5
if ($LASTEXITCODE -ne 0) { throw 'Volume measurement failed' }
```

`--roi X Y WIDTH HEIGHT` 是原生深度图中的测量窗口。本次只选择承载面上煤块所在区域，
排除下方反光区和相机视野以外的背景；窗口参数不是煤块的人工轮廓。
更换相机位置或煤块分布后需要调整窗口。贴近窗口边缘的目标会在 JSON 中标记 partial_roi。

## 算法与单位

1. 预热 30 帧，采集 15 帧静态场景。每像素取非零深度中位数，有效样本不足 60% 则保留为空。
2. 使用 SDK 内参/畸变模型反投影。把彩色图对齐到原生深度坐标，仅用于叠加标注。
3. 在窗口内拟合承载面：RANSAC + 最小二乘，平面内点距离阈值 2 mm。
   用户提供的 285 mm 是平面拟合的合理性参考，不强制覆盖实测高度。
4. 提取离承载面高度超过 5 mm、时间中位绝对偏差不超过 2 mm 的点；
   做 3×3 开运算，去除小于 200 像素的区域。5 mm 是本次抑制背景起伏的阈值，
   会使薄块和靠近地面的边缘偏小。
5. 对窄接触处用距离变换核心和最近核心归属分开。轮廓由真实分割掩膜生成，
   接触区域的分界是几何估计，应核对图片。此流程不做煤/矸石材质分类。
6. 建立承载面的正交坐标，在 1 mm × 1 mm 网格上累加平均离地高度 × 实际面积。
   共享边界格按样本比例分配面积，避免重复计算。输出单位 cm³。
   不补造缺失深度。JSON 记录内部空格、深度空洞和 0.75/1/1.25 mm 网格敏感性。

这里的体积遵循已确认的假设：上表面与承载面之间全部计入体积，包括底部凹陷或悬空。
本次没有独立的标准体积真值，因此不宣称实际体积误差已标定。
网格覆盖率只统计被观测点包围的内部空格，不代表所有边缘都完整，也不是测量准确率。

## 2026-10-30 实测记录

以下路径是当时采集电脑上的运行输出，源码仓库仅记录验证结果。

- 原始数据：`runs/volume_live/capture_20261030_a/`，含 15 帧原始深度、射线、彩色图和标定 JSON。
- 最终测量：`runs/volume_live/measurement_20261030_final/annotated.png`。
- 完整数值与边缘坐标：同目录 `measurements.json`。
- 标签和高度数组：同目录 `geometry.npz`；二值掩膜 `mask.png`。
- 实测承载面距离 285.35 mm，平面内点 RMS 0.627 mm。
- 5 个目标体积约 76.9、27.6、63.4、53.5、2.9 cm³，总计 224.3 cm³。

## 数值校验

```powershell
python -X utf8 -m unittest discover -s tests -v
```

测试覆盖已知棱柱体积、重复点、共享面积不重复累加、倾斜平面含前景离群点、
接触分离与空深度不补值。真实相机采集已完成；这些数值测试不替代标准体积校准。
