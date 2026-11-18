# WT Voice Forge

本地运行的 War Thunder（战雷）语音包制作工具。添加录音、训练音色、生成语音，再打包应用到游戏。

**当前版本：0.9.21 · Windows 10/11 x64 · MIT**

本项目为非官方社区工具，与 Gaijin Entertainment 无隶属或背书关系。War Thunder 及相关商标归各自权利人所有。

## 下载与安装

前往 [Releases](https://github.com/bearyluo/WTVoiceForge/releases) 下载：

- `WTVoiceForge-Setup-0.9.21.exe`：安装版，运行后按向导安装。
- `WTVoiceForge-Portable-0.9.21.zip`：绿色版，解压到可写文件夹后运行其中的 exe；请保留随附许可文件。
- `SHA256SUMS.txt`：文件校验值。

使用发布的程序不需要安装 Python。程序使用本地 WebView2 窗口；缺少 WebView2 时会提供安装入口。安装包尚未代码签名，Windows 可能显示未知发布者或 SmartScreen 提示。

首次打开会进入“环境准备”，集中检查游戏目录、官方 FMOD 工程、FMOD Studio CLI 和语音运行时。GPT-SoVITS / RVC 按需下载，不包含在安装包里；运行时下载约 5–8 GB，解压、训练和构建还需要额外空间，下载前会检查可用空间。

## 四步制作

1. **添加素材**：添加参考录音，试听、整理、裁静音和切片。
2. **训练音色**：训练自己的音色并试听，也可以导入已有 GPT-SoVITS 或 RVC 模型。
3. **生成语音**：选择国家和场景，编辑台词，批量生成或原语言换声；逐句试听、重做，或直接导入现成 WAV。
4. **应用到游戏**：构建 `.wtvoice` 包，导出分享，或备份后安装到游戏 `sound/mod`。

已有音色和已生成的音频可以复用，不必每次从头制作。

## 主要功能

- **中文配音**：通过 GPT-SoVITS 克隆或微调音色，修改台词并合成中文。
- **原语言换声**：通过 RVC 保留原语言内容并转换音色。
- **范围选择**：通用无线电、空战、陆战、海战、座舱告警及部分可选提示音；10 个科技树快捷选择，61 张乘员语音表。
- **逐句编辑**：原音对比、连续试听、定位、续听、循环、重新生成及当前候选选择。
- **声音调整**：中文配音支持参考片段、语速和音量设置；原语言换声支持音量调整。可应用到当前筛选的多句。
- **模型导入**：GPT-SoVITS `.pth` + `.ckpt`；RVC `.pth` 和可选 `.index`。导入新模型会使当前项目已生成的音频失效，请先保存需要保留的版本。
- **语音包管理**：导入 `.wtvoice`、ZIP、RAR / 7z，查看包内内容，检查同名 bank 冲突，备份安装、卸载及恢复原版音效。
- **本地存储管理**：查看空间占用，清理缓存与日志，单独卸载语音运行时。

## 数据、安装与恢复

项目、训练模型、生成音频、缓存和导出包保存在应用旁的 `data/`。升级安装保留该目录；建议另行备份重要项目和模型。

安装语音包时会备份被覆盖的文件；游戏运行时拒绝安装。安装主要写入 `sound/mod`，必要时调整 `config.blk` 的音效配置，不修改游戏内存。

软件内卸载有“保留 data 文件夹”选项，请明确勾选后再继续。通过外部卸载程序卸载时，会询问是否同时删除数据；删除不可恢复。静默卸载默认保留数据，显式传入 `/DELETEAPPDATA` 才删除。

应用处理素材和训练在本机进行；下载运行时、打开外部下载页面等操作需要联网。用户素材、模型、个人配置、原始聊天记录和游戏音频不会提交到本仓库。

## 使用边界

- 本版本作为预发布提供。自动测试通过不代表所有机型、语言和游戏场景均已实听验证。
- 构建需要用户本机的官方 FMOD 工程及 FMOD Studio CLI；二者不随软件分发。游戏更新后可能需要重新检查兼容性和安装语音包。
- 海战支持 7 张表，内置 4,981 条首轮中文译法，需要听原音校对；目前没有可用的官方中文海战语音表。
- FMOD 2.02.28 与官方工程 2.02.22 已检查版本系列及 bank 头一致，完整游戏内效果仍需验证。
- RVC 跨语言试听材料已生成，尚未完成玩家的人耳评分；换声质量与素材、训练和原语言有关。
- 已编译的 FMOD bank 不做二进制合并。多个包覆盖同一个 bank 时会提示冲突，不能承诺按句自动合并。
- 部分工程比游戏素材旧；缺失内容以实际检查结果为准，不能保证所有提示音都能替换。
- 同一项目重新训练可能覆盖原有模型；保留多版时请先备份。当前没有独立的跨项目模型库。
- 参考录音应取得授权；公开分享语音包前，请确认音色、台词和音频素材的使用权限。

## 源码运行

Python 3.10+，使用标准库本地 HTTP 服务；开发者已在 Python 3.13 验证，本次发布检查在 Python 3.12 跑通 257 项自动测试。

```powershell
git clone https://github.com/bearyluo/WTVoiceForge.git
cd WTVoiceForge
python -m app
```

打开 `http://127.0.0.1:8765`。默认只监听本机，不应直接暴露到公网。

运行自动测试：

```powershell
python -m unittest discover -s tests -q
```

桌面打包需要额外安装 PyInstaller、pywebview 和 pythonnet：

```powershell
python -m pip install pyinstaller "pywebview>=6.2,<7" "pythonnet>=3.2,<4"
```

## 构建 Windows 程序

在 Windows 上构建；安装包另外需要 Inno Setup 7。以下路径只是示例，可自行替换。

```powershell
# 安装版使用目录模式，exe 与 _internal 必须一起保留
powershell -ExecutionPolicy Bypass -File packaging\build_portable.ps1 -Python python -OutDir D:\WTVoiceForge-build -Mode onedir

# 生成安装包，可通过 -Iscc 指定 Inno Setup 编译器路径
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -ExeDir D:\WTVoiceForge-build -OutDir D:\WTVoiceForge-release

# 可选：单文件绿色版
powershell -ExecutionPolicy Bypass -File packaging\build_portable.ps1 -Python python -OutDir D:\WTVoiceForge-portable -Mode onefile
```

构建脚本下载并校验 vgmstream，打包前端资源和应用文本数据。代码签名参数已提供，但需要发布者自己的证书。

## 项目结构

```text
app/          本地服务、音色训练、合成、构建和安装逻辑
app/data/     台词、用途说明与翻译文本
web/          桌面窗口中的前端页面
tests/        单元及接口测试
packaging/    Windows 构建和安装器脚本
tools/        语音表审计、翻译与验证辅助工具
docs/         设计决策和研发记录（包含历史描述）
vendor/       第三方组件许可；二进制在构建时下载
```

## 许可与反馈

应用代码采用 [MIT License](LICENSE)。第三方组件、图形资源和音频工具使用各自许可，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。游戏原始音频、官方 FMOD 工程、语音运行时和模型权重不在本仓库分发。

问题反馈请提交到 [Issues](https://github.com/bearyluo/WTVoiceForge/issues)，附上程序版本、复现步骤和经过脱敏的错误信息。请勿公开上传账号凭据、个人录音或未经授权的游戏素材。
