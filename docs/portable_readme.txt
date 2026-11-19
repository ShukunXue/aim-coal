煤块实时体积测量 — Windows 64 位便携版

开始使用
1. 将整个压缩包解压到一个可写文件夹。
2. 接好 RealSense D435i，关闭 RealSense Viewer 和其他占用相机的程序。
3. 双击 CoalVolume.exe，等待相机预热和承载面拟合。
4. 在测量窗口内移动煤块，轮廓和体积会实时更新。

快捷键
R：重新拟合承载面（相机位置变化后使用）。
B：在完整彩色图中选择测量区域；Enter 确认，Esc 取消。
S：保存当前标注图、原始深度、标签和体积数据。
Q / Esc / 关闭窗口：退出并释放相机。

默认参数
深度/彩色 640×480，15 fps；承载面参考距离 285 mm。
测量区域 X=90，Y=78，宽=430，高=257（原生深度坐标）。
1 mm 网格高度积分；前景高度阈值 5 mm；最小区域 200 像素。
相机或承载面位置变化后应按 R；煤块超出测量区域时可按 B 调整。

输出
程序旁的 runs\volume_realtime\时间戳\：
latest_status.json：定期更新的体积、轮廓框和运行状态。
latest_preview.png：定期更新的标注画面。
plane.json / session.json：承载面、相机参数和本次配置。
按 S 后生成 snapshot_*.png、snapshot_*.json 和 snapshot_*.npz。

注意
请保留 CoalVolume.exe 旁的 _internal 文件夹，不要只复制 exe。
此便携版包含 Python、NumPy、OpenCV 和 RealSense Python 运行库，无需 Anaconda。
适用于 Windows 64 位及已能正常识别的 RealSense D435i；中文界面使用系统微软雅黑字体。
体积按“上表面到承载面之间全部计入”的已确认方法估算，单位 cm³。
编号按当前帧重新排序；PARTIAL 表示目标越过测量边界，体积不完整。
不要同时开启多个测量实例。
如启动失败，错误提示会显示详细日志路径。

命令行参数和 Python 调用方式见同目录“调用说明.md”。
