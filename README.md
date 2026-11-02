# PANORAMA Mapper · 曲面视频工作台

把普通视频预变形为适合 TRYX PANORAMA SE / KANALI 上传的 **2160×1080（2:1）** 展开画面，从指定眼位观看时恢复平面构图。支持铺满裁切、完整视频立牌、拖动取景、GPU 映射、Blender 工程与严格小于 **500,000,000 字节** 的 MP4 交付。

An offline, single-view anamorphic video mapper for the TRYX PANORAMA SE curved display. Chinese local UI, adjustable viewpoint/crop, GPU acceleration, editable Blender scenes and a strict 500 MB MP4 delivery gate. Independent community project; not an official TRYX product.

## 快速开始

1. 从 [Releases](https://github.com/kaminosora/kanali-video/releases) 下载 Windows x64 便携包，完整解压到有写入权限的文件夹。不要直接从 ZIP 运行或只复制 EXE。
2. 双击 **Start-Panorama.cmd**。便携包无需另装 Python。首次启动会提示从 PyPI 下载官方 PyAV 媒体组件，安装在包内 `.deps`；此后可离线使用。源码版需要 Python 3.13 x64，首次安装固定版本依赖到项目 `.deps`。
3. 浏览器打开本机面板。运行环境区可搜索已安装程序，或手动填写 `blender.exe` 和 `ffmpeg.exe`。空值为自动查找；相对路径以程序目录为基准。
4. 缺少依赖时，从 [Blender 官方下载页](https://www.blender.org/download/) 安装 Blender 4.5 LTS，从 [FFmpeg 官方 Windows 构建入口](https://ffmpeg.org/download.html#build-windows) 下载含 `libx264` 的 FFmpeg。面板也有下载链接。
5. 退出时运行 **Stop-Panorama.cmd**。只关闭浏览器不会停止后台任务。

搜索覆盖 PATH、程序旁的 `tools`、常见安装目录及 Windows 注册表登记位置，不遍历整块硬盘。自定义安装目录未找到时手动指定即可。配置只写程序旁的 `settings.json`，不修改系统 PATH、驱动或代理。

## 取景与播放

1. 选择本地视频/图片、拖入素材或填写路径。路径导入只读原片；拖入上传在 `inputs` 留副本。
2. 用内置网格按 [校准指南](docs/CALIBRATION.md) 设置眼位。默认水平 **45°** 是两翼平分方向，俯视 **10°**、距离 **650 mm** 仅是起点。
3. **铺满取景**：拖动原片预览中的可见轮廓，滚轮缩放；暗区是被裁掉的内容。无法继续平移时先放大。
4. **立牌小屏**：保留完整视频并添加边框、厚度感和底座。切换模式重置取景，保留眼位和曲面参数。
5. 对照目标眼位的已校正预览，再看曲面展开图。展开图本来就应有预变形；普通平屏播放成片不是最终观感。
6. 设置入点/出点，选高质量 MP4、自动加速、2× 超采样。先导出短片实机检查，再做完整片段。
7. KANALI 选整个屏幕的单画布显示，导入完成卡片中的 MP4，检查没有再次裁切、缩放或附加叠加。

输出无音轨，最高 60 fps，自动保留源有理帧率。预览是缩小代理，最终导出使用完整素材。普通二维视频不会获得真实双目深度；头部离开设计眼位后可能出现拉伸和折感。铺满约束不会去掉原片内部黑边，需要主动放大裁切。

## 上传画布与旧版成片

v1.1.1 起，以用户实际 KANALI 的 **2:1 上传窗口**为准，展开预览、GPU/Blender 渲染、MP4 和归档母版统一使用 **2160×1080、方形像素（SAR 1:1）**。官网面板标称的 2240×1080 与软件上传画布是不同概念；不能直接用前者决定后者。

v1.1.0 的 2240×1080 成片不要再交给 KANALI 居中裁切，这会丢失曲面映射两端的 UV 范围。优先用原素材和已保存的参数在新版重新导出；若只保留了旧成片，可将**整幅展开图缩放**为 2160×1080 并设 SAR 为 1:1，随后重新检查 500 MB 限制。不要把已预变形的成片再次作为普通素材做一遍曲面映射。

上传栅格改为 2:1 不会自动改动曲面的毫米尺寸、G2 过渡或眼位。已有校准参数仍保留，实际物理尺寸继续按测量调整。

## 500 MB 约束

- 按实际帧数与帧率估算 **480 MB** 目标码率并预留容器开销。估算不等于最终体积保证。
- 高质量候选完成后检查实际字节数，只有 **`0 < size < 500_000_000`** 才能成为成功结果。
- 超限则对候选自动做 H.264 两遍码率控制，仍超限再降低预算，最多三轮。此步骤使用 CPU x264，无需重做三维映射。
- 重试失败、取消或仍超限会明确报告，不交付超限文件作为完成结果。较长视频受码率预算约束会损失细节；有需要时缩短片段。再次编码也会增加有损压缩损失。
- **FFV1 / MKV 母版**通过 Blender 16 位渲染、无损编码用于归档，不受 500 MB 约束，体积可能很大，不能直接作为 KANALI 交付。

## 加速与兼容性

自动模式实际建立 OpenGL 3.3 上下文并绘制，再实际试编码，依次尝试 NVENC、QSV、AMF，最后 CPU x264。编码器列表存在不等于硬件可用。面板可手动选择；指定不可用设备会明确报错。

快速路径在 GPU 上执行与 Blender 相同的投影网格和 UV，直接把 RGB 帧送入编码器，避免逐帧写 PNG。Blender 仍保存可编辑工程；自动 GPU 路径失败时回退 Blender。Eevee 本身也使用 GPU，旧流程耗时还包括 PNG16 写盘、读回和 CPU 编码。

快速路径为 8 位 SDR；Blender 母版保留 16 位处理。滤波和抗锯齿有少量差异，不承诺逐像素相同。NVIDIA RTX 5080 已实机验证；其他设备按实际探测结果启用，Intel/AMD 独立显卡未实机验证。详见 [硬件说明](docs/HARDWARE.md)。

有效高约 73.6 mm、展开宽约 152.65 mm、弯折 90°、G2 过渡弧长 14 mm 都是可调假设，不是厂家曲线实测。[来源记录](research/SOURCES.md) 解释官方规格和推算边界。HDR/PQ/HLG/Dolby Vision、未支持广色域及部分高位深输入会被拒绝，请先转成 SDR。

## 中间文件与复现

每次导出创建独立 `outputs/时间-名称-编号/`。其中成片、`panorama.blend`、`render-settings.json`、`first-frame.png`、`render.log` 和 `job.json` 用于交付与诊断；MP4 另有 `delivery.json` 记录体积及重编码情况。

视频先规整成无损 `work/source.mkv` 供精确时间轴和 Blender 工程使用。常见 8 位 YUV 保留原采样格式，避免膨胀为 RGB16；无损片仍可能很大。任务报告准备进度及中间文件体积。**500 MB 限制只针对最终 MP4**，先截取需要的片段可节省磁盘。

不要删除工程引用的 `work`。只需要成片时，可在备份后自行删除整个任务目录。工程三个场景对应展开输出、目标眼位校正和未校正对照。改变眼位后应回面板重新生成映射。

程序没有固定机器路径，但个人运行生成的工程会记录所选素材绝对位置；迁移电脑后需要重定位素材和外部程序。发布包不含个人设置、素材、预设或日志。

## 故障与开发

缺程序时在面板搜索或手动指定；FFmpeg 需有 `libx264` 以保证预算重编码及回退。进度不动时查看任务日志与 `.cache/logs/server.log`；浏览器断开不等于渲染失败，WinError 10053 不能单凭错误号归因于 TUN。GPU 不可用可选择 Blender / CPU，远程桌面或虚拟机可能不支持所需 OpenGL。

```powershell
python scripts/setup_deps.py
python entrypoint.py
python -m unittest discover -s tests
node --test web/*.test.cjs
```

测试按环境变量、PATH 或本地设置定位 FFmpeg。启动面板后运行 `python scripts/verify_pipeline.py` 可做合成素材真实导出回归。发布检查见 [v1.1.1 验收](docs/VALIDATION-1.1.1.md)，构建见 `python scripts/build_release.py --help`。

服务只监听 `127.0.0.1`，媒体不上传云端。自有源码采用 MIT；第三方组件各自许可证见 [声明](THIRD_PARTY_NOTICES.md)。
