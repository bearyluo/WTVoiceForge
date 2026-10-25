# vgmstream 运行时

这里存放随绿色版分发的 vgmstream 官方 Windows 64 位运行时。

- 上游：https://github.com/vgmstream/vgmstream
- 版本：r2117（2026-05）
- 发布包：`vgmstream-win64.zip`
  - URL：https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip
  - 大小：4,468,180 字节
  - SHA-256：`6c4a8a3813864fefed081bbd337dbc0ad93bf88e0b92f5db98d7ab258b22dc6c`
- 许可：ISC；完整文本见同目录 `COPYING`，其他第三方库见项目根目录 `THIRD_PARTY_NOTICES.md`。

## 刷新/准备

```bash
py -3.13 packaging/fetch_vgmstream.py --dest vendor/vgmstream --force
```

脚本会下载、校验 SHA-256、安全解压，并验证 `vgmstream-cli.exe` 可以运行。

## 注意

- `.exe` / `.dll` / `.zip` 已被 `.gitignore` 排除，不提交进仓库。
- 打包脚本会在构建前自动准备本目录；目标机器不需要 Python。
- 源码模式若本目录缺失，应用会把同样的官方包下载到 `<data>/components/vgmstream/`。
