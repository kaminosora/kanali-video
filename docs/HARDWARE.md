# 显卡、编码器与移植性

快速模式把共享的曲面 `SourceUV` 网格交给 OpenGL 绘制，直接将 RGB 帧送给 FFmpeg，省去逐帧 PNG 写盘与重新解码。Blender 仍负责可编辑工程；无损母版保留 Blender 的 16 位处理链。

## 硬件支持范围

| 路径 | 要求与探测 | 本轮验证范围 |
| --- | --- | --- |
| OpenGL 快速渲染 | 创建 OpenGL 3.3 独立上下文，实际绘制并读回一个像素 | Windows、RTX 5080、驱动 610.88 已实测 |
| NVIDIA H.264 NVENC | FFmpeg 与驱动支持，并通过小片段真实编码 | 已实测 |
| Intel H.264 QSV | FFmpeg 与驱动支持，并通过小片段真实编码 | **未在 Intel 实机验证** |
| AMD H.264 AMF | FFmpeg 与驱动支持，并通过小片段真实编码 | 本机混合显卡环境的短编码探测通过；**AMD 独显整链未验证** |
| CPU H.264 x264 | FFmpeg 包含 libx264 并通过真实编码 | 已实测 |
| Blender EEVEE | Blender 能在当前驱动环境启动并渲染 | Blender 4.5.4 LTS、RTX 5080 已实测 |

OpenGL 渲染通过 ModernGL/glcontext，Windows 使用 WGL，代码没有 CUDA 专用依赖，也不按显卡品牌拒绝 AMD/Intel。跨品牌接口和选择逻辑已有模拟测试；这不等于对应显卡已经通过实机验收。本机另有 AMD Radeon(TM) Graphics（Windows 驱动 32.0.21042.62），便携包已通过实际视频解码、NVIDIA OpenGL 映射、AMD AMF 编码的 6 帧导出。但 OpenGL 渲染器明确是 RTX 5080，不能据此声称 AMD OpenGL 或 AMD 独显整链通过。[ModernGL 上下文说明](https://moderngl.readthedocs.io/en/latest/topics/context.html)、[glcontext Windows 后端](https://github.com/moderngl/glcontext)说明了独立上下文的创建方式。

显卡型号或 FFmpeg 的 `-encoders` 列表只能说明候选能力。程序还会实际创建渲染上下文、读回帧缓冲，并试编码短片；只有探测通过的编码器才可自动选用。NVENC、QSV、AMF 是互相独立的候选，CPU x264 是编码回退选项。

自动模式下，OpenGL 快速路径不可用时改用 Blender；硬件编码不可用时可选择已通过探测的 CPU x264。手动强制一个不可用选项会给出错误。这里的“回退 Blender”不是纯 CPU 渲染保证：Blender 自身仍需满足其显卡与驱动要求。

如果驱动让独立探测进程崩溃或超时，主进程不会再次加载 OpenGL，而是单独启动 FFmpeg 验证 CPU x264；验证通过后保留 Blender+x264 路线，同时显示原探测失败原因。

软件 OpenGL（包括 llvmpipe、softpipe、SwiftShader、SWR、GDI Generic、Microsoft Basic Render Driver）不会被标为硬件加速。远程桌面、虚拟机或不完整的显卡驱动可能影响检测；以本机探测结果为准。

## 画质与颜色边界

- 快速模式输入和输出为 **8 位 SDR RGB**。GPU 使用线性光三次采样，然后转回 sRGB；支持原生分辨率和 2× 渲染后缩小。
- `frame_rgb()` 按源帧标签区分 BT.709、BT.601、FCC、SMPTE240M 及 limited/full 范围；RGB 输出统一为 full range。没有矩阵标签的普通 YUV 保持 swscale 的传统 BT.601 假设，不伪造已确认的 BT.709 源标签。
- 已验证 PyAV 16.0.1 在颜色参数相同时可能跳过转换器配置的行为，并对 full-range YUV 做了针对性处理；测试覆盖 TV 黑白、full 灰阶、709/601 彩色、8/16 位灰度范围和可信 RGB16 中间源。[对应 PyAV 源码](https://github.com/PyAV-Org/PyAV/blob/v16.0.1/av/video/reformatter.pyx)。
- HDR、PQ、HLG、Dolby Vision 等仍由媒体层拒绝，快速路径不隐式 tone map。
- **无损母版自动使用 Blender + FFV1**，不会先降为 GPU RGB8 再宣称 16 位无损。
- GPU 与 Blender 的抗锯齿、边界采样和颜色量化有差别，不保证逐像素相同；透视映射和重采样也不等于“零损失”。

## 本机小样测量

测试环境为 Python 3.13、ModernGL 5.12.0、glcontext 3.0.0、PyAV 16.0.1、Blender 4.5.4 LTS，RTX 5080 / NVIDIA 610.88。ModernGL 报告实际 NVIDIA 渲染器；Blender 完成 EEVEE 渲染后，其进程也出现在同一 NVIDIA GPU 的进程表中。旧流程本来已使用显卡，新路径主要减少逐帧输出和编码前处理。

下面是一个 60 帧、1 秒 SDR 片段的独立原型测量：4480×2160 GPU 渲染，再由 FFmpeg Lanczos 缩到 2240×1080。视频解码预先完成，因此本表**不是完整任务耗时或长视频速度保证**。

| 编码设置 | 渲染、传输与编码耗时 | 帧吞吐 | 片段大小 |
| --- | ---: | ---: | ---: |
| NVENC p7 / HQ / CQ 14 | 0.96 秒 | 62.5 fps | 6.24 MB |
| x264 slow / CRF 14 | 1.80 秒 | 33.3 fps | 3.18 MB |

同一样本预先解码及转 RGB 约 0.46 秒。一次 GPU 同步测量约为：纹理上传 0.93 ms、绘制 0.50 ms、读回 2.92 ms。读回和进程间传输仍有成本，当前不是全程零拷贝流水线。

NVENC 的 CQ 14 与 x264 的 CRF 14 **不是同一质量标尺**，上表不表示二者画质相等；本样本的文件大小也不同。用户可根据实际画面、时间和文件体积选择编码模式。

旧 Blender 2× 小样中，三帧的单帧渲染约 0.33–0.35 秒，16 位 PNG 写出约 0.51–0.52 秒，文件读回约 0.01 秒。该比较用于定位瓶颈，不把短样本线性外推为整段任务承诺。

合成校准图的 GPU/Blender 2× 对照结果：铺满模式平均通道误差 **0.104/255**，99.28% 通道差值不超过 2；立牌模式平均误差 **0.326/255**。细线和边缘的局部最大差值较大，后者不能只凭平均误差认定完全一致。最终整链验证见 [v1.1.0 验收记录](VALIDATION-1.1.0.md)，实体屏幕验收见 [CALIBRATION.md](CALIBRATION.md)。

## 安装与迁移

发布代码不预设作者的用户名、盘符或程序安装路径。界面通过本机发现结果和用户选择配置 Blender、FFmpeg；本机路径保存在自己的 `settings.json`，不应拿作者的私有配置分发。

源码运行需要 Python 3.13 64 位和项目依赖；项目安装脚本将依赖放在本项目 `.deps`，缓存留在项目内。独立发布包是否包含运行时，以该包的 README 为准。不要把某台机器的显卡 DLL、系统目录或个人缓存复制给其他用户。

测试工具位置可由 `FFMPEG_PATH` / `BLENDER_PATH` 指定，其后查 PATH、最后读取本地 `settings.json`，不带开发机器的绝对路径兜底。媒体测试在没有 FFmpeg 时明确跳过；GPU 像素测试在没有可用 OpenGL 时也明确跳过。运行编码和交付测试前请先配置 FFmpeg；模拟测试不会被写成硬件实测。

官方依赖与说明：

- [Python Windows 下载](https://www.python.org/downloads/windows/)
- [Blender 下载](https://www.blender.org/download/)
- [FFmpeg 下载及 Windows 构建入口](https://ffmpeg.org/download.html#build-windows)
- [ModernGL PyPI](https://pypi.org/project/moderngl/)、[glcontext PyPI](https://pypi.org/project/glcontext/)
- [ModernGL 帧缓冲读回接口](https://moderngl.readthedocs.io/en/latest/reference/framebuffer.html)
- [NVIDIA FFmpeg 硬件加速说明](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html)
- [FFmpeg QSV 编码器说明](https://ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders)
- [AMD AMF 的 FFmpeg 设置说明](https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/AMF-Encoder-Settings-and-Tuning-in-FFmpeg)

本文件不包含用户视频的文件名、路径、帧图或可还原的像素数据。
