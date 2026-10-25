# 调研与实测记录

这份文档记录 bank/事件/官方工程/RVC DirectML 的核实结果，作为 M1/M2 的依据。路径用占位符表示，不写本机绝对路径。

## 1. 战雷本机结构

已验证的战雷目录形态：

```text
<War Thunder>/
  aces.exe
  config.blk
  sound/
    _crew_dialogs_common_zh.assets.bank
    _crew_dialogs_ground_zh.assets.bank
    aircraft_gui.assets.bank
    aircraft_guns.assets.bank
    tanks_effects.assets.bank
    ...
    mod/                 # 已安装的语音包
      .manifest.json
```

`config.blk` 里与 mod 相关的字段：

```text
sound{
  fmod_sound_enable:b=yes
  speakerMode:t="auto"
  enable_mod:b=no
}
```

安装语音包时需要把 `enable_mod` 改成 `yes`，但 M0 只读，不写。

## 2. bank 体积与分类

本机实测的关键 bank：

| bank | 体积 | 内容 |
|---|---:|---|
| `aircraft_gui` | 3.7 MB | 空战 VWS、雷达/导弹告警、击杀/助攻提示、失速/迎角告警 |
| `aircraft_guns` | 35 MB | 机炮、导弹发射/飞行、音爆 |
| `tanks_effects` | 29 MB | 陆战效果、击杀镜头、弹道/命中 |
| `_crew_dialogs_ground_*` | 12~20 MB | 陆战乘员语音，按语种分文件 |
| `_crew_dialogs_common_*` | 7~15 MB | 陆战通用乘员语音，按语种分文件 |
| `event` | 24 MB | 通用事件 |

注意：体积会随游戏版本变化，不能写死；M1 以实际扫描为准。

## 3. 可替换事件（实测字符串）

### `aircraft_gui.assets.bank`

确认存在：

```text
vws_lock
vws_lock_cockpit
vws_missile
vws_missile_launch
vws_warning
radar_lock
missile_launch
enemy_destroyed
ui_kill_assist
stall_warning-001/002/003
stall_warning_piston-001/002/003
attack_angle_warning_us
attack_angle_warning_ussr_1/2
critical_event_warning
next_target
```

未找到 `rwr_*` 形式的事件名。RWR 的听觉告警大概率由 `radar_lock`、`vws_lock`、`missile_launch` 等事件承担；部分提示可能由引擎实时合成，不能保证全部可替换。

### `aircraft_guns.assets.bank`

确认包含 `missile_start` 相关素材。官方工程对应素材目录：

```text
Assets/weapons/rockets_missiles/
  missile_start_heavy-001..003.wav
  missile_start_light-001..003.wav
  missile_start_light_far-001..003.wav
  missile_start_med-001..003.wav
  missile_start_med_far-001..003.wav
  missile_sonic_boom-001..004.wav
```

### `tanks_effects.assets.bank`

确认包含 `killscene` 与 `shell_flight`。官方工程对应素材目录：

```text
Assets/tanks/gui/killscene/
  killscene_blast_ammoexplode_1.wav
  killscene_pierce_1..3.wav
  killscene_rico_1..3.wav
  killscene_rico_hitmodules_1..8.wav
  killscene_shellfly_1.wav
  killscene_shot_1.wav
  killscene_xray_on.wav
```

## 4. 官方 FMOD 工程

已验证的官方工程（Gaijin，约 2.1 GB 本地解压）：

```text
<kit>/
  sounds.fspro
  Assets/
    ambient/ ath/ common/ dialogs_wopl/ dialogs_wt_ships_2022/
    dialogs_wt_tanks_2023/ engines/ explosion/ explosive/ fx/ gui/
    radio_chat/ ships/ tanks/ vws/ weapons/
  Metadata/
    Bank/*.xml
    Event/*.xml
    Asset/*.xml
```

关键点：

- `Assets/dialogs_wt_tanks_2023/` 只有 `english_uk`、`english_us`、`german_new`、`russian_new` 四个陆战素材槽位；
- `Metadata/Bank/*.xml` 记录 bank GUID → 名称；
- `Metadata/Event/*.xml` 记录事件名和 `banks` 关系，例如 `enemy_destroyed` 的 bank GUID 指向 `aircraft_gui`；
- `tanks_effects` 的 GUID 是 `{7f5d1e9e-c52c-4d3b-81f6-491fc9654e65}`，`shell_flight` 事件指向它；
- `Assets/vws/betty`、`Assets/vws/rita`、`Assets/vws/xiao906` 是三套 VWS 语音素材；
- `Assets/gui` 下有 `enemy_destroyed`、`missile_launch`、`radar_lock`、`alarm_launch-*`、`missile_alarm_*` 等素材。

构建命令：

```text
fmodstudiocl.exe -build -ignore-warnings <project>.fspro
```

已安装的 FMOD Studio CLI 在本机 `C:\Program Files\FMOD SoundSystem\FMOD Studio 2.02.28\fmodstudiocl.exe`；程序会做常见路径探测，不写死版本号。

### 目录解析实测（M0）

对官方工程跑 Event → Bank 解析：

- `Metadata/Bank`：92 个 bank XML；
- `Metadata/Event`：12,256 个事件 XML；
- 其中 10,458 个事件有明确的 `banks` 关系；
- 1,798 个事件没有 `banks` 关系（引擎启动、机炮、命中、语音消息等嵌套事件），首版标记为 `unassigned`，单独计数，不进入 bank 覆盖率；
- 事件数最多的 bank：`crew_dialogs_naval` 1462、`aircraft_engines` 1114、`tanks_engines` 905、`crew_dialogs_ground` 807、`crew_dialogs_common` 744、`tanks_weapons` 459、`aircraft_gui` 273。

这验证了“不把分类写死、从官方工程自动生成目录”的路线可行。M1 再补素材路径、时长和覆盖率。

## 5. 陆战语种改名策略

- 官方工程只有 4 个陆战素材槽位，但游戏里有 26+ 个 `_crew_dialogs_ground_<lang>` bank。
- 已安装的“纳西妲（正式版）”包中，`_crew_dialogs_ground_zh.bank` 与英文版 `.bank` 字节一致（MD5 `c81358b7cf4a4473080febfa18e63289`），说明“用英文槽位构建，再把产出的 bank 改名成目标语种”在至少一个旧版本上成立。
- 当前游戏版本（2026-10）仍需 M1 实测：构建一个测试 bank → 改名成 `_crew_dialogs_ground_zh` → 放进 `sound/mod` → 进游戏听。
- 失败备选：在工作副本里给目标 bank 增加 AudioTable，或使用社区模板；不把未验证方案写死。

## 6. vgmstream 解包

命令形态（圆桌工程使用）：

```text
vgmstream-cli -S 0 -o <out>/?n.wav <bank>
```

- `-S 0` 表示按 stream index 命名；
- 提取出的文件名用于和官方工程素材做 basename 映射；
- vgmstream 是 ISC 许可，后续把 Windows 二进制放进 `vendor/vgmstream/`，并在 `THIRD_PARTY_NOTICES.md` 记录。

## 7. RVC 的 DirectML 现状

截至 2026-10 的官方 RVC 仓库：

- `configs/config.py` 里有 `_detect_directml()`、`--dml` 参数和 `torch_directml.device()`；DirectML 下强制 `fp32`、`is_half=False`；
- `webui.py` 的 F0 提取在 DirectML 下走 `extract_f0.py dml`；
- HuBERT 特征和推理使用 `config.device`，可以走 DML；
- `train/train.py` 仍然以 `torch.cuda.is_available()` 和 `.cuda()` 为主；DML 下模型会留在 CPU，**官方原版没有真正把训练跑在 A 卡上**。
- 因此 A 卡训练需要固定 RVC 版本 + 补丁桥接：`.cuda()` → `.to(device)`、设备判断走 `config.device`、关闭 AMP/GradScaler、保持 fp32；预处理器/F0/HuBERT 继续用官方 DML 路径。
- DirectML 还要求 `assets/rmvpe/rmvpe.onnx`。

## 8. 聊天里确认的取舍

- v1 只做“制作语音包”主链路；装包器、GPT-SoVITS、云端、市场后置。
- v1 的自由度优先于“预设齐全”：任意 bank/事件、拖拽映射、自定义分类、高级模式。
- N 卡 4GB 轻量训练、2GB 换声；A 卡 4GB 换声、6GB 训练 Beta。
- 不承诺所有 RWR 音都能替换；以官方工程事件为准。

## 9. RVC 官方整合包（2026-10 核对）

RVC 最新 Release 为 `2.3.260718`（2026-07-18）。GitHub Release 本身没有附件，官方整合包放在 Hugging Face 仓库 `lj1995/VoiceConversionWebUI` 根目录；下面的字节数和 SHA-256 来自 Hugging Face LFS 元数据。

| 包 | 文件 | 大小 | SHA-256（前 16 位） | 用途 |
|---|---|---|---|---|
| NVIDIA 稳定版 | `RVC20240604Nvidia.7z` | 5,599,416,462 B / 5.6 GB | `86dfc8ef3d00274a` | GTX 10 ~ RTX 40 |
| NVIDIA RTX 50 | `RVC20260718Nvidia50x0.7z` | 7,606,183,207 B / 7.6 GB | `d258bc0cbcce0713` | RTX 50 系 |
| AMD / Intel | `RVC20260718AMD_Intel.7z` | 5,575,283,376 B / 5.6 GB | `cdd7938a9be24566` | DirectML |

- 官方地址格式：`https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/<文件>?download=true`，支持 Range 请求，可断点续传。
- 国内镜像：`https://hf-mirror.com/lj1995/VoiceConversionWebUI/resolve/main/<文件>?download=true`。镜像内容必须用同一个 SHA-256 校验，校验不过就换源。
- 仓库 README 说明源码版需要 Python 3.12；整合包已自带运行环境，用户不需要另装 Python/torch/CUDA。
- 解压：Windows 优先 7-Zip；没有 7-Zip 时用 `C:\Windows\System32\tar.exe`（bsdtar，支持 7z 读取）。下载 + 解压同时存在，按约 2.5 倍包体积检查磁盘。
- 官方整合包内含的第三方依赖、模型和音色素材分别遵循各自许可；本项目只下载、不重新分发，并在发布绿色版时保留 RVC 的 MIT 许可与来源说明。
- 本机实测：7-Zip 位于 `C:\Program Files\7-Zip\7z.exe`；用 518 字节的测试 zip 走通了下载 → 校验 → 解压 → 自动识别 → 写入配置的完整链路，但未实际拉取 5.6GB 正式包。

## 10. 参考

- 战雷圆桌工程（MIT）：https://github.com/Oxygen-copper/war-thunder-roundtable
- Gaijin 官方 FMOD 工程：https://github.com/GaijinEntertainment/fmod_studio_warthunder_for_modders
- RVC（MIT）：https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
- GPT-SoVITS（MIT）：https://github.com/RVC-Boss/GPT-SoVITS
- vgmstream（ISC）：https://github.com/vgmstream/vgmstream
- DirectML：https://github.com/microsoft/DirectML
- 战雷音效模组 Wiki：https://wiki.warthunder.com/Sound_modifications

## 11. M1 目录/解包实测（2026-10-23）

### 目录

- `Metadata/Event` 12,256 个 XML，解析约 5–7 秒；10,458 个事件有 bank 归属，1,798 个未分配/嵌套。
- 7,022 个有归属事件带 audioFile 引用；去重后 48,775 条（原始关系 49,220 条）。
- 有归属的唯一 AudioFile GUID **28,933**；全部事件（含未分配）共 **34,789** 个唯一 GUID。
- `Metadata/AudioFile` 66,088 个 XML，记录 `assetPath`、`length`、`channelCount`、`frequencyInKHz`。
- 官方 `Assets` 原始为 49,326 个 `.wav`，其中 **49,319 个是 Ogg Vorbis**，只有 7 个 RIFF WAV；试听必须按魔数判断类型。
- 补全无线电语音后 `Assets` 为 **64,594 个 `.wav`**；有归属的 28,933 个唯一素材中 **28,918 个存在，15 个缺失**，7,022 个带引用事件全部至少有一条可试听素材。
- 剩余 48 个缺失：15 个 `aircraft_guns` 的 `weapons/grains/t160_*` 颗粒，33 个未分配事件的效果/乘员提示音（23 个 `shells_pass_by`、10 个 `dialogs_wt_tanks_2023/md`）。游戏 bank 中已找不到同名流，保留缺失标记。
- 完整目录缓存约 14.8 MB；并行读取 AudioFile 后构建约 12–24 秒（受磁盘缓存影响），串行曾达 4 分钟。
- 验证样例：`enemy_destroyed` → `aircraft_gui` / `gui/enemy_destroyed.wav` / 3.185 秒；`shell_flight` → `tanks_effects` / `tanks/gui/killscene/killscene_shellfly_1.wav` / 7.993 秒（7 条素材取最长）。
- 覆盖率按“有 bank 归属的事件”统计：**7,022 个有可试听素材，0 个素材全缺失**。

### 社区/游戏 bank 补全无线电语音

- 官方仓库 README 明确写明不含 crew chat、其他 monologue 和音乐；`Assets/radio_chat` 只带 `eng`、`ru`，其余 17 个前缀只有 `Metadata/AudioFile` 路径。
- 社区教程和语音包基本都基于同一份不完整工程；没有找到可信的完整源 WAV 镜像。
- 游戏 `sound/dialogs_chat_<lang>.assets.bank` 的 FSB5 流名与工程 `assetPath` 的文件名逐条一致（如 `dialogs_chat_de` 的 `ge_bailout_v2_r4_t1_mood_low`），时长也与 `AudioFile` 元数据吻合。
- 已用 `tools/fill_radio_chat.py` 从 16 个语种 bank 补入 **14,333** 个文件；`hchn` 无独立 bank，按与 `chn` 完全一致的 935 条元数据时长复制。总计补入 **15,268** 个文件，缺失从 15,316 降到 48。
- 补全后 `Assets/radio_chat` 的音频是 vgmstream 解出的 PCM16 WAV；原工程声源原本是 Ogg-in-wav，FMOD 重新导入没有格式问题，但文件体积会变大。

### vgmstream / 解包

- 官方 r2117 win64 zip：4,468,180 字节，SHA-256 校验通过。
- 对 `aircraft_gui.assets.bank`（3.7 MB，FEV/FSB5）实测：
  - `-S 0 -o ?n.wav` 只生成 226 个文件（324 个流里有重名）；
  - `-S 0 -o ?s_?n.wav` 生成 **324** 个标准 RIFF WAV，总大小 42.1 MB、总时长 7:04；
  - 流名可直接用于匹配，例：`103_enemy_destroyed.wav`、`107_vws_attack_air.wav`。
- 解包输出在 `<data>/unpacked/<bank>`；manifest 记录流号、名字、文件、大小、WAV 时长和匹配结果。
- best-effort 匹配 `aircraft_gui`：324 个流中 160 个匹配到目录素材（60 个同名、若干归一化匹配，其余未匹配），匹配不作为权威覆盖率。
- 中文乘员 bank 改名仍需随真实 FMOD 构建在 M3 验证。

## 12. GPT-SoVITS 运行时实测（2026-10-23）

- 上游仓库：https://github.com/RVC-Boss/GPT-SoVITS （MIT）
- Windows 整合包仓库：https://huggingface.co/lj1995/GPT-SoVITS-windows-package
- 使用包：`GPT-SoVITS-v4-20250529.7z`
  - 7,493,794,100 字节；SHA-256 `6461419c7c81f7c23a08e467c040eb25197bf8291ec1107e0104c46354b77146`（本机 `certutil -hashfile` 与清单一致）；
  - 解压后 50,358 个文件、约 13.4 GB；包含 `runtime/python.exe`、`api_v2.py`、`GPT_SoVITS/configs/tts_infer.yaml`、`GPT_weights_v4/`、`SoVITS_weights_v4/`；
  - 自带 FunASR 中文/粤语识别模型（`tools/asr/models/`）、chinese-roberta、chinese-hubert、s1/s2 预训练权重。
- API 启动：`runtime/python.exe api_v2.py -a 127.0.0.1 -p <随机端口> -c GPT_SoVITS/configs/tts_infer.yaml`；本机 RTX 3060 Laptop 6 GB 首次就绪约 30–90 秒。
- `/tts` 参数（实测可用）：`text`、`text_lang`、`ref_audio_path`、`prompt_text`、`prompt_lang`、`text_split_method=cut5`、`batch_size=1`、`media_type=wav`、`streaming_mode=false`、`speed_factor`、`parallel_infer=true`。
- 参考音频限制：上游要求 3–10 秒，2.04 秒的样本被 400 拒绝，错误为“参考音频在3~10秒范围外”。
- 参考文字必须准确：用错误的 prompt 文本时，12 字台词可能只生成 0.76–1.0 秒；换成与参考音频逐字一致的文本后，输出明显更完整。
- 随机种子会早停：上游默认 `seed=-1` 时，同一句 12 字实测出现 0.92 / 0.80 / 3.72 秒；固定 seed=42 三次都是 3.44 秒，seed=1234 三次都是 2.12 秒。应用改为按“台词 + 参考文字”计算固定种子，并在时长低于字数下限时换种子重试最多 3 次；同一句重复合成时长一致。
- FunASR：
  - 上游 `tools/asr/funasr_asr.py` 的 VAD 路径对游戏 WAV 返回空结果（`IndexError: list index out of range`）；
  - 禁用 VAD、直接用 `speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` 可正常识别；
  - 输入必须是 16 kHz 单声道；应用用 Python 标准库线性重采样，不额外装 ffmpeg。
  - 实测 `chn_chat_5_r2.wav` 转写为“航行正常时间准确目标无误一切状况良好完毕”，与音频一致。
- 样例合成：12 字中文、参考 5.42 秒，输出 32 kHz 单声道 WAV，约 2.1–5.4 秒（随种子/语速）；RMS 约 2061、峰值 16736，不是静音。5–22 字多句实测时长随时间增长，没有静音或空文件。
- 尚未验证：多段素材微调训练、A 卡、长文本（数百字）、并发合成。

## 13. GPT-SoVITS v4 微调实测（2026-10-23）

- 素材：6 段中文无线电（5 段 `chn_chat` + 1 段 `chn_aircraft_destroyed`），总时长 23.5 秒；文字用包内 FunASR 自动识别后写入训练名单。
- 数据准备：
  - `1-get-text.py` 生成 `2-name2text-0.txt`（6 行）；
  - `2-get-hubert-wav32k.py` 生成 `4-cnhubert/` 和 `5-wav32k/`；
  - `3-get-semantic.py` 生成 `6-name2semantic-0.tsv`；
  - 应用把 part 文件合并为 `2-name2text.txt` 和 `6-name2semantic.tsv`（训练脚本只认这两个名字）。
- 训练参数：v4 LoRA（`s2_train_v3_lora.py`），s2 2 轮，`batch_size=1`、`grad_ckpt=True`、LoRA rank 32；s1 8 轮（`s1longer-v2.yaml`，batch 1）。0.6.1 起 s1 改为 15 轮，对齐官方 WebUI 默认。
- 时间：s2 每轮约 1 分 23 秒，总训练约 8 分 19 秒；GPU 显存峰值约 4.9 GB（RTX 3060 Laptop 6 GB）。
- 产物：`SoVITS_weights_v4/<exp>_e2_s192_l32.pth`（约 75 MB）、`GPT_weights_v4/<exp>-e8.ckpt`（约 155 MB）。
- 权重切换：`/set_sovits_weights`、`/set_gpt_weights` 均返回 200；日志显示 v4 LoRA 加载成功。切换后 12 字合成 3.48 秒；切回 v4 底模后 19 字合成 4.08 秒。
- 踩坑：
  - 内置 Python 的 `python39._pth` 会忽略 `PYTHONPATH`，且包内 `users.pth` 仍指向打包者的百度网盘路径；必须用 `runpy` 显式注入 `sys.path`。
  - 训练脚本要求合并后的 `2-name2text.txt` / `6-name2semantic.tsv`，只跑 part 文件会在 `TextAudioSpeakerLoaderV4` 的 `assert os.path.exists(self.path2)` 失败。
  - 上游 `g2pw` 在 onnxruntime 1.19.2 打印 `preload_dlls` 异常，但被 try/except 捕获，文本特征仍正常生成。
  - 相对路径传给 `/tts` 会以 API 的工作目录解析；应用必须传绝对路径。

## 14. FMOD 构建与 `.wtvoice` 实测（2026-10-23）

- **环境**：FMOD Studio 2.02.28（`fmodstudiocl.exe`），官方工程 `fmod_studio_warthunder_for_modders`；只读游戏 `sound` 和工程。
- **构建命令**：`fmodstudiocl.exe -build -banks aircraft_gui -platforms Desktop -shared-audio-source-dir <build/assets> -ignore-warnings sounds.fspro`。
- **工程体积**：`Metadata` 91,715 个文件 / 230.7 MB；`Assets` 64,594 个文件 / 2.6 GB。首次构建复制元数据到项目目录，之后复用。
- **素材暂存**：只暂存 `aircraft_gui` 和 `masterbank` 事件引用的素材；目标 bank 的 `source.path` 用合成 WAV 覆盖。硬链接失败时回退复制。
- **产物**：`Build/sound/aircraft_gui.bank`（280,782 字节，RIFF 头）与 `aircraft_gui.assets.bank`（3,409,056 字节，FSB5，317 条流）。`.wtvoice` 3,691,571 字节。
- **替换核对**：vgmstream r2117 读取 `aircraft_gui.assets.bank`，`stream index 99 / stream name enemy_destroyed`，8,000 Hz 单声道、8,000 samples、1.000 秒；原事件素材长度 3.185 秒，说明替换已烘焙进 bank。
- **masterbank 依赖**：只暂存局部素材时 FMOD 报 `Failed to build bank "masterbank" ... A required file could not be found`，但目标 `aircraft_gui` 仍成功产出。应用以目标 bank 文件是否存在判定成功，`masterbank` 不进包、沿用游戏原版。
- **耗时**：元数据已缓存后，单 bank 构建 + 打包 96 秒。
- **待验证**：游戏内 `sound/mod` + `enable_mod:b=yes` 的实际加载、bank 覆盖关系、陆战/VWS 套装替换。当前结论只到“bank 产物正确、替换流可核对”。

## 15. 本地化乘员语音表实测（2026-10-24）

- **AudioTable**：`Metadata/Bank` 中 14 个 bank 带 `sourceDirectory`：通用乘员 de/en/ru、陆战 de/en/en_us/ru、海战 de/en/en_us/fr/it/jp/ru。官方工程没有中文 AudioTable 源目录。
- **行数差异**：美军陆战工程文件 1208 个（604 个唯一流名）；游戏 `_crew_dialogs_ground_en_us.assets.bank` 有 1256 条流 / 628 个唯一流名；工程缺 32 个名字、48 条变体流。中文 `_crew_dialogs_ground_zh` 为 655 个唯一流名；美军比中文多 5 个名字（drone、fire_engine_v1、radiator_damaged_v1/v2/v3），中文表多出 32 个名字。
- **构建方式**：把 `Assets/dialogs_wt_tanks_2023/english_us` 整棵目录硬链接到 `build/assets`，映射替换后镜像到 `build/base/Assets`，再跑 FMOD。AudioTable bank 不读取 `-shared-audio-source-dir`；只给 shared 目录时产物只有 494 字节空 bank。
- **游戏新增流**：从工程缺的流名里，优先用同语言其他目录（如 `english_uk`）的原音；没有再按流序号从游戏 bank 提取。vgmstream `-S 0` 一次解出 1256 条流约 5 秒；逐条 `-s` 会反复解析 bank，明显更慢。
- **产物**：单句验证 `_crew_dialogs_ground_en_us.bank` 494 字节 + `.assets.bank` 12,509,216 字节；完整 623 句官方中文复用构建 `.wtvoice` 14,822,475 字节 / `.assets.bank` 14,573,600 字节，1272 条流 / 636 个唯一流名；`voice_message_artillery_1` 为中文原音 0.504 秒；5 条未翻译的新增行保留原音（如 `driver_fire_engine_v1` 1.629/1.014 秒）。首次构建 315–316 秒，包含 230 MB 元数据复制与游戏原音提取。
- **界面**：选中美军陆战表显示 628 个流名、32 条工程新增流；整表加入 628 句；原音接口返回 `audio/ogg`（工程 `.wav` 实际是 Ogg Vorbis）。
- **官方中文复用**：`_crew_dialogs_ground_zh.assets.bank` 解出 655 个名字；与美军同名匹配 623 条，提取+复制 7.8 秒，剩余 5 条为 `commander_drone`、`driver_fire_engine_v1`、`driver_radiator_damaged_v1/v2/v3`，与“美军比中文多 5 条”的对比结果一致。
- **待验证**：游戏内加载 `_crew_dialogs_ground_en_us` 覆盖包、中文项目补齐后的音频与文本一致性、海战/通用乘员实听。

## 16. 语音包安装器实测（2026-10-24）

- **测试环境**：假战雷目录（`win64/aces.exe`、`sound/`、`config.blk`）+ 含 2 个 bank 和 manifest 的 `.wtvoice`，避免动真实游戏安装。
- **上传**：`.wtvoice` 823 字节通过原始 POST 上传并解析出 2 个 bank、2 条台词样例；存储到数据目录 `packs/uploads/<12位编号>/source.wtvoice`，同时写入 `pack.json`。
- **安装**：目标 `sound/mod` 写入两个 bank，`config.blk` 的 `enable_mod` 从 `no` 改为 `yes`；原配置备份到 `packs/backups/<时间>_<编号>/config.blk`，安装记录含每个 bank 的 SHA-256。
- **冲突展示**：安装前两个 bank 均为“新增”；安装后重新检查显示“mod 中已是同一文件”；`sound/mod` 里已有的第三方 `existing_ui.bank` 未被计入安装记录，也不会被删除。
- **卸载**：删除两个本工具 bank，`installed.json` 清空，第三方 bank 保留；因为安装前 mod 中没有同名文件，没有可还原项。
- **边界**：同一 bank 会被后安装的包整体覆盖；旧包记录保留但标记“已被改动/覆盖”，卸载时 SHA 不匹配就跳过。真实游戏内加载仍需实听。

## 17. 官方社区安装/制作步骤核对（2026-10-24）

- **官方 wiki（Sound modifications）**：安装步骤为下载模组 → 在 `config.blk` 的 `sound{}` 添加 `enable_mod:b=yes` → 在游戏 `sound/` 下创建 `mod` → 把模组文件解压进去 → 启动游戏。部分模组还要求 `useLoopsForGunSound:b=yes`（快速射击飞机炮循环）或 `useLoopsForGMGunSound`（地面/舰船炮）。还原时把 `enable_mod` 改成 no（或删除该行）并删除 mod 文件。
- **官方 wiki（制作）**：需要 FMOD Studio，从官方仓库下载/克隆工程，用当前 FMOD Studio 打开；不要改事件名、文件位置、目录层级，也不要在 bank 里改事件组成。官方仓库 Readme 还要求不要改 mixer bus 结构。
- **官方 FMOD 工程 Readme**：当前标注 FMOD Studio **2.02.22**；仓库素材为节省体积压成 OGG/Vorbis（扩展名改为 .wav），工程全局压缩为 Vorbis 37%，重复压缩会明显掉音质。工程不包含乘员聊天、独白和音乐。2026-09-16 更新到 Sky Odyssey；2022-04-01 起 bank 拆成 `.bank` 元数据和 `.assets.bank` 音频。旧 `hangar.bank` 留在 mod 目录可能造成超级响等随机后果。
- **社区常见配置**：WT Live 多个语音包安装说明要求 `sound{ fmod_sound_enable:b=yes enable_mod:b=yes }`，有的还写 `speakerMode:t="auto"`；成员组语音包要求游戏设置 → 声音 → 成员组语言选“游戏选项”或“成员组国籍”。
- **社区更新经验**：音效包作者和 wt-sound-volume 工具说明都指出，游戏更新后 `sound/mod` 里的旧 bank 仍会覆盖新版同名文件，需要删除/重装；带 `masterbank` 字样的文件在部分版本会导致无声、爆音或卡顿，社区建议从 mod 目录删掉（不能删游戏 `sound` 根目录的）。
- **本应用对应实现**：所有写入仍只发生 `sound/mod` 和用户确认后的 `config.blk`；包内改为 `sound/mod` 结构；支持上述 config 开关；比较原版/模组 mtime 做更新提示；识别 `masterbank.*`/旧 `hangar.bank`；成员组语言和“左上角已启用自定义音效”做成安装后清单。
- **版本差异**：本机 FMOD Studio CLI 为 2.02.28，官方工程当前标注 2.02.22；同属 2.02，但尚未做真实游戏内加载验证。构建页提示差异，若游戏不识别 bank 应换用官方标注版本。

## 18. W.A.V.E. 降噪包资料与安装实测（2026-10-24）

- **W.A.V.E. 是什么**：社区作者赤风RED 的 “Wide Adapted Voice Enhance”，用于提升战雷语音质感/去除成员组语音无线电底噪，兼容原版语音和基于原版修改的语音包，通常与二次元语音包配合。来源：WT Live 作者页（`post/1136813`，W.A.V.E. 4.0）和 wtliker 资源 7767。
- **文件组成**：社区说明列出 `crew_dialogs_ground.assets.bank`、`crew_dialogs_ground.bank`、`crew_dialogs_naval.assets.bank`、`crew_dialogs_naval.bank`、`masterbank.assets.bank`、`masterbank.bank`；要求全部安装以保证功能完整和稳定。
- **masterbank 的作用与风险**：作者说明 masterbank 是制作降噪包必不可少的文件，旧语音包枪炮声消失/卡顿/崩溃多数由 masterbank 引起；W.A.V.E. 4.0 是兼容性修复版。旧版 W.A.V.E. 可能导致部分枪声/引擎声静音。
- **安装方式**：与普通语音包一样放进 `sound/mod`，并需要 `enable_mod:b=yes`（社区通常也写 `fmod_sound_enable:b=yes`）。
- **本应用策略**：不重新分发 W.A.V.E.；解析时允许识别但标记 protected，安装默认拒绝，用户明确允许后才写 masterbank；安装前备份、卸载还原。
- **实测**：构造 4 个 bank 的 W.A.V.E. 风格包（masterbank + masterbank.assets + crew_dialogs_ground + crew_dialogs_ground.assets），解析得到 `requires_opt_in=True`、`wave_like=True`；未允许安装时报错且 mod 目录无文件；允许后安装成功、卸载清除。真实游戏内叠加语音包尚未实听。

## 19. 本地化语音表逐语种覆盖实测（2026-10-24）

- **方法**：用 vgmstream 读游戏 `sound/_crew_dialogs_*.assets.bank` 的 stream name 并去重，与官方 FMOD 工程的 AudioTable 素材目录逐名对比；中文源为游戏 `_crew_dialogs_common_zh`（720）、`_crew_dialogs_ground_zh`（655）、`_crew_dialogs_ground_sm_zh`（639）。
- **通用**：de 720/720 全有中文；en 738 条、缺 18；ru 738 条、缺 18。
- **陆战**：de 695 条、中文覆盖 638、缺 57；en 641 条、覆盖 640、缺 1；en_us 628 条、覆盖 627、缺 1（`voice_message_commander_drone`）；ru 621 条、覆盖 620、缺 1（`voice_message_commander_target_SAM_`）。
- **海战**：没有任何 `_crew_dialogs_naval_zh`，7 张表（de 702、en 819、en_us 718、fr 702、it 706、jp 709、ru 625）全部需要翻译+合成；工程流名比游戏少 19–132 条，构建时必须保留游戏新增流。
- **补充表**：`ground_sm_zh` 的 639 个名字能补上美军 4 条（`driver_fire_engine_v1`、`driver_radiator_damaged_v1/v2/v3`）、英军 7 条、德军 6 条、俄军 4 条。此前只用 `ground_zh` 得到的“美军剩 5 条”并不完整。
- **未收录**：游戏 `sound` 里还有 44 张 `_crew_dialogs_*` 本地化表（common_cz/fr/hu/it/jp/ko/pl/pt/sp/sr/th/tr/vi、ground_ar/cz/el/fi/fr/gl/he/hi/hu/id/it/jp/ko/lt/nl/nw/pl/pt/sp/sr/sv/th/tr/vi 等）不在官方工程中；缺少官方 AudioTable metadata，应用只报告不可生成，不伪造 bank。
- **性能**：一次统计 14 张表（vgmstream 元数据）实测 38.8 秒；同一服务进程内后续调用复用 `game_streams` 缓存。UI 因此改为按钮按需统计，而不是页面加载即统计。

## 20. 海战语音表与翻译任务实测（2026-10-24）

- **数据源**：游戏 `sound/_crew_dialogs_naval_{de,en,en_us,fr,it,jp,ru}.assets.bank`，vgmstream 读 stream name 后按名字去重；官方工程 AudioTable 作为流名对照。
- **规模**：7 张表 702/819/718/702/706/709/625 条唯一流名，合计 4981 条；并集 904 个名字，7 张表都有的 575 个。去重后总时长约 20.7/23.8/18.9/18.8/25.4/16.8/16.4 分钟（同一名字在不同语言下时长不同）。
- **中文源**：不存在 `_crew_dialogs_naval_zh`。陆战中文补充表与海战有 7 个同名通用流（move_forward/backward/stop 的 v1/v2 和 commander_target），可选用但音色可能不一致。
- **重复结构**：`voice_message_{commander|officer|sailor}_*`；commander 286 个名字、officer 361、sailor 256，另有 1 个 `Bearing_4_for_Italian_Addition`。数字档位非常密集：角度 10–360（每个角色 37 左右）、深度 20–600m 与 65–1950ft、方位 20–360、距离 500–15000m/yd。
- **分组结果**：4981 条流 → 904 个唯一名 → 247 个任务；12 个数字模板覆盖 2152 条，235 个短句任务覆盖 2829 条。v1–v7 等变体共用一个译文任务，但构建时每条流仍需单独合成。
- **任务包**：`D:\WTVoiceForge\翻译任务\海战_2026-10-24`，含分组任务（247 行）、流名索引（904 行，含 7 种语言流序号/时长）、7 张逐条 CSV、术语表、统计 JSON 和 README。首次生成约 15 秒（vgmstream 逐张读取）。
- **验证**：`app/translation.py` 单测覆盖数字模板、变体合并、去重、CSV BOM；API ZIP 已验证 4 个条目和 en_us 718 行逐条任务。

## 21. GPT-SoVITS 效果不满意时的排查结论（2026-10-24）

- **官方默认与数据建议**：官方 v4 WebUI 默认 SoVITS 2 轮、GPT 15 轮、LoRA rank 32；官方 README 建议微调约 1 分钟音频、零样本约 5 秒。v3/v4 的相似度和稳定性更高，但 v4 对参考音频更敏感；v2 对脏数据/口音更宽容。来源：项目内置 `GPT-SoVITS/docs/cn/README.md`、`webui.py` 和官方 v3/v4 特性说明。
- **并行推理吞字**：GitHub issue #2658 报告“有 prompt_text 时，多长的文字都会被吞到 2–3 秒”，用户实测关闭 `parallel_infer` 后恢复。应用此前固定 `parallel_infer=true`，0.6.1 改为 `false`。
- **重复/吞字/参考串音**：issue #1644 的建议是降低轮数、加长过短的合成文本、换种子；issue #636 的实测结论是素材质量比参数更重要，专业录音明显好于自制/剪辑素材，3 分钟仍偏机械，10 分钟接近真人。最终采纳“干净素材 > 准确参考文字 > 合适轮数 > 参数微调”的顺序。
- **v3 金属音/电音**：issue #1691、#2466、#2246 有用户反馈 v3 金属感/电音，部分人用 RVC 后处理压制；数据质量和轮数仍是主因。v4 官方说明修复了 v3 的金属音问题。
- **战雷社区现状**：WT Live 多个 GPT-SoVITS v4 语音包作者明确提示“可能咬字模糊/带电音”，素材多来自游戏/动画而非干净录音；社区常用 W.A.V.E. 改善成品底噪。说明成品质量的天花板首先由素材决定。
- **本次回退**：0.6.1 默认 1 版；多版本不再轮换参考、不再放宽采样，只换种子；GPT 15 轮；关闭并行推理。多版本参考轮换与 `top_k` 20/30、温度 1.1 的实验保留在 M3.9 记录中，不再作为默认。
- **参考链接**：<https://github.com/RVC-Boss/GPT-SoVITS/issues/2658>、<https://github.com/RVC-Boss/GPT-SoVITS/issues/1644>、<https://github.com/RVC-Boss/GPT-SoVITS/issues/636>、<https://github.com/RVC-Boss/GPT-SoVITS/issues/1691>。

## 22. 真实游戏台词 A/B：8 轮 vs 15 轮（2026-10-24）

- **目的**：不用演示句，直接用会进语音包的真实游戏台词，对比 8 轮和 15 轮 GPT 微调；同时解出官方中文原音做对照。
- **样本**：6 条，覆盖空战/陆战、长句/短句、受损/维修：`base_player_inZone_air_v1`、`mission_complete_victory_04_1_v1`、`voice_message_driver_damaged_v1`、`voice_message_gunner_barrel_damaged_v1`、`voice_message_commander_repair_needed_v4`、`voice_message_gunner_rotation_drive_damaged_v1`。
- **控制变量**：涂山苏苏 48 段 / 144.3 秒同一批参考；同一参考 `039_nav_09.wav`、同一 prompt、同一随机种子、`top_k=5`、`top_p=1`、温度 1、关闭并行推理；8 轮模型为原项目，15 轮模型在复制的临时项目重训。
- **时长**：8 轮 4.20 / 2.24 / 1.72 / 1.48 / 3.20 / 1.68 秒；15 轮 4.32 / 2.08 / 1.36 / 1.52 / 3.24 / 1.76 秒；官方中文原音 3.07 / 2.02 / 1.19 / 1.44 / 2.52 / 1.48 秒。两者都比原音慢，节奏接近。
- **ASR 字面相似度**：8 轮 100 / 100 / 66.7 / 50 / 100 / 60，平均 79.5%；15 轮 100 / 100 / 54.5 / 100 / 90.9 / 20，平均 77.6%。15 轮修好了“炮管受损”（50→100），但把“我们挨了一发”和“方向机受损”识别得更差（“我买了一服”“发相机搜索”）；长句两版都基本完整。
- **结论**：15 轮不是万能药，短句上甚至可能更不稳。轮数不是主要矛盾，参考音频、逐字文字和素材质量仍是上限；官方 15 轮保留为默认，但不再宣传为“更稳”。两套模型和真实台词试听保留在 `D:\WTVoiceForge\质量对比_2026-10-24\`，ASR 只用于发现吞字/重复，不能代替人耳听感。
- **产物**：`试听对比.html`（游戏原音 / 8 轮 / 15 轮三轨）、`report.json`、`8轮/`、`15轮/`、`游戏原音/`；15 轮模型项目重命名为“涂山苏苏 · 15轮对照”，可直接在快速制作里选用。

## 23. 全语言乘员表与动态 AudioTable 实测（2026-10-24）

- **游戏本体**：`sound/` 里匹配 `_crew_dialogs_*.assets.bank` 的表共 61 张：通用 17、陆战 37（其中 `ground_sm_*` 补充表 6 张）、海战 7。语言代码去重 33 个（`ar, cz, de, el, en, en_au, en_us, en_za, fi, fr, gl, he, hi, hu, id, it, jp, ko, lt, nl, nw, pl, pt, ru, sp, sr, sv, th, tr, uk, us, vi, zh`）。海军不计入快速制作。
- **官方 FMOD 工程**：14 张 AudioTable / 7 个代码（common: de/en/ru；ground: de/en/en_us/ru；naval: de/en/en_us/fr/it/jp/ru）。差距主要是其他语言和 6 张补充表，工程里没有对应 Bank/AudioTable 元数据。
- **动态补元数据**：`dynamic_bank.ensure_metadata()` 在临时工程副本里查找同名 Bank；不存在时创建确定性 UUID5 的 Bank XML，并用 `relationship name="audioTable"` 指向根级 AudioTable。AudioTable 的 `sourceDirectory=Assets/dialogs_dynamic/<bank>`、`includeSubDirectories=true`。重复调用同一张表不会生成重复对象。
- **原音目录**：`dialogs_dynamic/<bank>/<stream_index>/<name>.wav`。同名流（游戏里常见 med/high 等变体）用序号子目录分开；FMOD 构建后流序号可能重排，但流名保留。实测法语陆战 `ground_fr`：654 个流名 / 1308 条变体，构建后 `.assets.bank` 仍为 1308 条流。
- **构建**：FMOD Studio CLI 2.02.28，命令 `-build -banks <bank> -platforms Desktop -shared-audio-source-dir <assets> -ignore-warnings sounds.fspro`；动态表必须镜像到工程自己的 `Assets/`，不能只依赖 shared-audio-source-dir。构建输出的 stream index 与游戏原 bank 不同，但名称一致；同名变体走同一套替换映射，实测替换后 `1/927`、`421/928`、`532/929` 的 WAV SHA-256 分别一致。
- **试听原音**：动态表不在官方 kit 的 Assets 里，`/api/audio/source` 先查 kit，失败后回退 `dynamic_bank.source_audio()`，按流名取第一条并用 `vgmstream -s` 缓存到 `data/cache/localized/<bank>/<index>_<name>.wav`。实测法语 3 条动态台词 HEAD 请求均返回 200 / `audio/wav`。
- **边界**：动态表依赖用户本机游戏 `sound` 目录；游戏更新后 bank 大小/流名可能变化，需要重新准备/构建。海军仍未接入快速制作。

## 24. RVC 原语言换声实测（2026-10-24）

- **运行时**：官方 `RVC20240604Nvidia` 整合包，自带 Python 3.9.13、torch 2.0.0+cu118，在 RTX 3060 Laptop 6GB 上可用；`vc_single` 的 speaker id 必须传整数 `0`，传模型名会报 `TypeError: new(): invalid data type 'str'`。
- **训练桥接**：`train_bridge.py` 执行 32k 预处理、RMVPE 音高、HuBERT 特征、filelist/config、`train.py -e <epochs> -sr 32k -f0 1 -bs 4 -te 40 -se 10 -pg f0G32k.pth -pd f0D32k.pth -l 1 -c 0 -sw 1 -v v2`，再建 FAISS 索引并复制 `voice.pth` / `voice.index`。3 段素材 / 1 epoch 实测 59 秒完成，文件生成正常。
- **数据切片**：优先用项目 `train/audio32k`，没有则用 `reference`；要求至少 3 段、总时长 8 秒。文件名用 `path.name.split('.')[0]` 读 F0 文件（`*.wav.npy`），否则会把静音切片当成训练数据。
- **推理**：`infer_bridge.py` 设置 `weight_root` / `index_root` / `rmvpe_root`，构造 `Config` 和 `VC`，调用 `vc_single(0, path, pitch=0, f0_method='rmvpe', index_rate=0.75, filter_radius=3, resample_sr=0, rms_mix_rate=1.0, protect=0.33)`，用 soundfile 写 32 kHz WAV。宿主按 JSON 行解析 stage/progress/done，取消时杀进程树。
- **原语言链路**：法语 `ground_fr` 取 3 句主变体（index 1/2/3）→ RVC 换声 → 写回 `audio/<line_id>.wav` → 动态 AudioTable 构建。输出 32 kHz 单声道，时长 0.48/0.58/0.60 秒，RMS 约 5413/3528/3700（非静音）。两套同名变体在成品 bank 中的哈希与对应换声文件一致。
- **质量边界**：RVC 只迁移音色，原语言发音/口音来自游戏原配音；低码率/有损游戏原音会放大齿音。当前没有自动质量评分；是否静音/截断可以程序检查，音色、情绪和齿音仍需人耳和游戏内实听。

## 25. 座舱 VWS 事件 bank 与社区做法核对（2026-10-24）

- **游戏 bank 实测**：`aircraft_gui.assets.bank` 有 324 条流 / 226 个唯一流名；其中 `vws_*` 是 63 个唯一告警词 / 161 条语音包变体（Betty、Rita、xiao906 各一套），其余 163 条是雷达/锁定/导弹、失速/迎角/过载、炮手、界面、战报等音效或播报。54 张乘员表里没有任何 `lock/radar/rwr/warning/tone/beep` 流，座舱告警不能从乘员表里找。
- **官方工程**：`Metadata/Bank/{d5bab5d0-c653-4e3b-a5f0-e5711c067d7d}.xml` 是 `aircraft_gui` Bank，但它没有 AudioTable；`Assets/vws/{betty,rita,xiao906}` 是 VWS 源素材，事件 XML 是普通 `SingleSound`。`vws_lock_cockpit` 等事件在 `gui/vws` 分类下，每个变体一个事件、一个 `audioFile`。
- **社区标准流程**：B站《战争雷霆机内语音告警系统音频MOD制作简明指北》（`read/cv26907604`）和 WT Live 中文 VWS 包（post 1098456、1135918、1166040）都是：替换 `Assets/vws` 对应 WAV → 在 FMOD 里调整 VWS 事件音频长度 → 构建 `aircraft_gui` → 把 `aircraft_gui.bank` + `aircraft_gui.assets.bank` 放进 `sound/mod`。这验证了“事件 bank 替换”而不是“补 AudioTable”。
- **为什么不补 AudioTable**：`aircraft_gui` 的游戏事件通过事件 GUID/事件名播放，AudioTable 只是按目录打包流；补表不能保证游戏事件改读新流，反而可能多打一份无用素材。乘员表则相反，本来就是 AudioTable，必须动态补元数据。
- **中文告警词**：官方 xiao906 是中文 VWS 音源，43 条；用本机 FunASR 听写后人工校正（例如“减速版开启”→“减速板开启”、“顶角过大”→“迎角过大”、“弹胜”→“弹射”），未覆盖的 20 种按事件名语义补短句。听写结果不随应用分发，只保留手工整理后的 JSON。
- **事件长度**：VWS 事件 XML 的 `SingleSound.length` 是固定值；实测直接把 0.8 秒中文写进原本 0.5–1.2 秒的事件会被截断。应用在构建前恢复官方事件 XML，再按新 WAV 时长重写对应 `SingleSound.length`，真实构建后 `vws_pull_up` / `vws_lock_cockpit` 三条变体的 `play duration` 均为 0.8 秒。
- **工程版本差异**：旧版官方工程比当前游戏少 7 条后加的 UI/热诱循环音效：`heatseeker_seek_loop`、`heatseeker_track_loop`、`ui_ammo_burn`、`ui_capsule_landing`、`ui_citadel_breach`、`ui_kill_assist`、`ui_rosket_start`。2026-10-01 官方工程补入 `enemy_kill_assist`/`ui_kill_assist`；0.8.2 对旧工程从游戏 bank 自动补回这一条，构建得到 318 条流，剩余 6 条仍逐条列出。
- **来源**：
  - 官方工程：<https://github.com/GaijinEntertainment/fmod_studio_warthunder_for_modders>
  - B站制作指北：<https://www.bilibili.com/read/cv26907604>
  - WT Live 中文 VWS 包：<https://live.warthunder.com/post/1098456/en/>、<https://live.warthunder.com/post/1135918/en/>、<https://live.warthunder.com/post/1166040/en/>
  - 战争雷霆 Wiki 音效 MOD 页：<https://wiki.warthunder.com/Sound_modifications>

## 26. 0.7.2 座舱三套原声与游戏流顺序核对（2026-10-24）

- **实测分布**：63 种 `vws_*` 中，晓906 覆盖 41 种（41 条素材）、贝蒂 61 种（61 条）、丽塔 59 种（59 条），合计 161 条；三种语言并非每条都齐。
- **流顺序**：游戏 bank 里同名流的第一条并不是固定语种。按时长与工程素材匹配后，旧预览取到的第一条分别为俄语 23 种、英语 22 种、中文 16 种，另有 2 种无法唯一匹配。这就是“试听原音却是俄语”的根因，不是成品里混入俄语。
- **修正**：逐素材记录 `game_index`（时长排列组合求最小误差，161 条全部匹配成功）；默认取晓906，界面可切换。中文模式仍将 161 条全部替换为同一条中文音频；未生成/失败的行才保留原音。
- **中文素材数**：官方 `Assets/vws/xiao906` 目录有 43 个音频文件，其中 41 个是游戏 bank 存在的 `vws_*` 名字；另外 2 个不与当前事件对应，未计入 63 种台词。
- **来源**：官方工程与社区包同 #25；语种命名（北约 Betty、苏联 Rita、中系 xiao906）与社区 VWS 包说明一致。

## 27. 0.7.3 座舱 163 条“非 VWS”逐条核对（2026-10-24）

- **声学核对**：把 `aircraft_gui` 的 156 条可解素材转成波形/频谱抽查。`radar_lock`、`missile_launch`、`special_contact`、`alarm_*`、`con_scan_*` 是窄带提示音/扫描音；`enemy_destroyed`（3.19 秒）、`wts_mission_complete`（4.96 秒）、`debrf_medal` 是爆炸/号角/音乐式音效；`gunner_*` 7 条是宽带语音。
- **社区做法**：VWS 包替换 `Assets/vws` 三套人声；雪风类 RWR 包替换扫描/新威胁/锁定/导弹/红外导引头音效；Mygo 等综合包还会换击杀音效、成员组语音、无线电、炸弹/导弹音效。提示音和战果音效多是用自制/截取音频替换，不是 TTS 文本。
- **本次取舍**：VWS 61 项逐条可选；炮手 7 条作为可选人声；击毁音效作为可选“音效改语音”，默认不动；RWR/锁定/导弹提示音、任务音乐、界面音效保留原音，共 155 条。`ui_kill_assist` 在旧版工程缺失，0.8.2 起由构建兼容层从游戏原音补回；0.8.3 起放入“战果播报（原为音效）”可选列表，默认不勾选、保留原音。
- **构建验证**：选“导弹来袭 + 炮手装填 + 击毁”三项，真实构建替换 7 个素材（VWS 2 条、炮手 4 条语气变体、击毁 1 条），共享 `enemy_destroyed` 的另一个事件也同步换素材；317 条流构建成功，事件长度按新 WAV 更新。
- **社区来源**：官方工程、B站 VWS 指北、WT Live 中文 VWS 包与 Mygo/雪风音效包说明；RWR/击杀音效替换方式见 <https://live.warthunder.com/post/1180760/en/>、<https://www.bilibili.com/video/BV15f5RzaEAk>。
