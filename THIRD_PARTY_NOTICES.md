# 第三方组件与许可

本项目代码采用 MIT 许可。以下第三方组件按各自许可使用或随绿色版分发；本文件不是法律意见。

## vgmstream（随 exe 分发）

- 项目：https://github.com/vgmstream/vgmstream
- 版本：r2117（2026-05）
- 发布包：`vgmstream-win64.zip`
  - 地址：https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip
  - 大小：4,468,180 字节
  - SHA-256：`6c4a8a3813864fefed081bbd337dbc0ad93bf88e0b92f5db98d7ab258b22dc6c`
- 许可：ISC。完整文本见 `vendor/vgmstream/COPYING`，主要条款：
  - 允许为任何目的使用、复制、修改和分发，前提是保留版权声明和许可声明；
  - 软件按“原样”提供，不附带任何担保。
- 该官方 win64 包同时包含 FFmpeg 库（`avcodec-vgmstream-59.dll`、`avformat-vgmstream-59.dll`、`avutil-vgmstream-57.dll`、`swresample-vgmstream-4.dll`），按 FFmpeg 的 LGPL 2.1+ 许可分发；本项目未修改这些库，源码与许可见 https://ffmpeg.org/legal.html 。
- 包内其他解码库（`libvorbis`、`libmpg123`、`libspeex`、`libcelt`、`libatrac9` 等）分别遵循其上游许可；本项目只原样使用 vgmstream 官方发布包，不重新编译或修改。
- 本项目不把 vgmstream 二进制提交进 git 仓库；构建时由 `packaging/fetch_vgmstream.py` 下载、校验并放入 `vendor/vgmstream/`。

## GPT-SoVITS（按需下载，不随包分发）

- 项目：https://github.com/RVC-Boss/GPT-SoVITS
- 许可：MIT。
- 应用只从 Hugging Face 官方仓库（`lj1995/GPT-SoVITS-windows-package`）下载官方 Windows 整合包 `GPT-SoVITS-v4-20250529.7z`，校验 SHA-256 后解压；不重新打包、不镜像托管。
- 包体：7,493,794,100 字节；SHA-256 `6461419c7c81f7c23a08e467c040eb25197bf8291ec1107e0104c46354b77146`；解压后约 13.4 GB。
- 包内自带 Python、PyTorch、预训练模型和 FunASR 中文/粤语识别模型。应用通过本机 `127.0.0.1` 调用包内 `api_v2.py` 的 `/tts` 接口，并用包内 FunASR 脚本做参考文字识别。
- 预训练模型/权重来自 GPT-SoVITS 官方发布，按其上游声明使用；本项目不分发、不修改这些权重，也不上传用户音频。

## RVC（按需下载，不随包分发）

- 项目：https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
- 许可：MIT。
- 用于保留原语言的音色转换，支持运行时下载、训练与已有模型导入；运行时和模型权重不随本项目安装包或源码分发。

## 7-Zip（仅调用，不随包分发）

- 项目：https://www.7-zip.org/
- 许可：LGPL（含 unRAR 限制）。
- 应用优先调用系统已安装的 7-Zip；没有时使用 Windows 自带的 `tar.exe` 或 Python 内置 `zipfile`。本项目不分发 7-Zip 二进制。

## 游戏与官方 FMOD 工程

- War Thunder 的游戏音频、Gaijin 官方 FMOD 工程的素材文件和 AI 模型/权重不随本项目分发。
- 为补回旧版工程缺失的击杀提示音，`app/kit_compat.py` 内嵌了官方工程的两个**元数据 XML**（`enemy_kill_assist` 事件和 `ui_kill_assist` AudioFile，不含音频）；来源见 <https://github.com/GaijinEntertainment/fmod_studio_warthunder_for_modders>。音频仍只在用户本机从游戏 `aircraft_gui.assets.bank` 按流名解出，不随包分发。
- 应用只在用户本机读取游戏 `sound` 目录；导出的语音包由用户自行确认音色授权与素材版权。
- 本项目不做内存修改、不碰反作弊，只读写 `sound` 和 `config.blk`。

## FMOD Studio（仅调用，不随包分发）

- 项目：https://www.fmod.com/studio
- 许可：FMOD Studio 及其 CLI 由 Firelight Technologies 提供，使用受其许可约束；本项目不打包、不再分发 `fmodstudiocl.exe`。
- 应用只调用用户本机安装的 FMOD Studio CLI 构建用户自己选择的官方工程；构建产物（bank）由用户自行使用和分发。
- 本项目不修改官方 FMOD 工程和游戏文件；构建缓存放在应用数据目录，可随时删除。

## 应用图标

- `packaging/app.ico`、`web/app.ico`、`web/app-icon.png`、`web/app-icon-128.png` 和安装器侧栏图使用项目原创的白发耳机女孩插画（AI 辅助绘制），不含第三方角色、商标或现成作品。
- 图标只在本地绘制和打包，不包含从网络下载的图片。

## Twemoji（界面贴纸）

- 项目：https://github.com/jdecked/twemoji
- 图形许可：CC BY 4.0，版权归 Twitter, Inc. 及其他贡献者；许可文本：https://creativecommons.org/licenses/by/4.0/
- 使用文件：`web/assets/emoji/1f3a7.svg`（耳机）、`1f338.svg`（樱花）、`1f493.svg`（爱心）、`2728.svg`（星光）、`2b50.svg`（星星）。
- 修改：仅通过 CSS 调整显示尺寸，SVG 内容未修改。

## Game Icons（战机 / 坦克线稿）

- 项目：https://github.com/game-icons/icons
- 图形许可：CC BY 3.0，许可文本：https://github.com/game-icons/icons/blob/master/license.txt
- 使用文件：`web/assets/icons/jet-fighter.svg`（作者 Delapouite）、`tank.svg`（作者 Lorc）。
- 修改：将 SVG 的 `currentColor` 填充改为深橄榄色 `#4d583c`，未改动路径。
