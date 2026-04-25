# Linux 中文语音输入法 · 单文件 351 行

> Linux 桌面上把中文「说」进去——按一下左 Alt 开始，说话，按 Enter 落字。
>
> 后端用 Groq 免费 tier 跑 whisper-large-v3，**€0 成本**。
>
> [English README](./README.en.md)

---

## 这是什么

一个 351 行 Python 单文件，把语音转成中文文字直接输入到当前光标处。

**Linux 上没有 Win+H 那种现成的中文语音输入法**，于是有了这个。

---

## 三个真实卡点 + 一个意外

> 这部分照实记开发心路。如果你也在搓自己的中文语音输入法，可以直接抄解法。

### ① Whisper 幻觉：静音也能"听"出字幕志愿者

第一次跑通后对着麦克风没说话，屏幕弹出「请大家点赞订阅」「字幕志愿者」「感谢观看」「music ♪」。Groq 的 whisper-large-v3 训练数据混了大量 YouTube 字幕，静音输入会触发幻觉。

**解法**：17 条幻觉黑名单（[`HALLUCINATION_PATTERNS`](./voice-input.py#L43)）+ 双重过滤 [`is_hallucination()`](./voice-input.py#L72) + [`clean_text()`](./voice-input.py#L84)。

### ② 延迟难受：讲完一整段，才一次性听到结果

v1 是说完一整段才整段送 Groq。一段 30 秒中文要等 5-10 秒才出字。一边讲一边盯屏幕，体验比手打还累。

**解法**：[webrtcvad](https://github.com/wiseman/py-webrtcvad)（Google 开源 WebRTC 语音活动检测）按自然语气停顿（约 400ms 静音）切段，每段独立异步发 Groq、立刻吐字。从「录完才出文字」变成「边说边出」。见 [`do_record_and_stream()`](./voice-input.py#L159)。

### ③ 热键累：三键组合要两只手

原快捷键 `Ctrl+Alt+V`，左手按 Ctrl+Alt，右手按 V。一天用 50+ 次，左手腕真的酸。

**解法**：左 Alt 单键 + 手写状态机。左 Alt 按下后期间只要碰过其他键就标 dirty，释放时 dirty=False 才触发——所以 `Alt+Tab` `Alt+F4` 等组合不会误触发。见 [`on_press / on_release`](./voice-input.py#L317)。

### 💡 意外发现

3 个卡点里，原以为系统级快捷键最难改（快捷键不都是写死在 OS 里的吗？）。结果它最简单，写一个监听 + 状态机就完事。

**评估难度的直觉，往往跟实际难度反着来。**

---

## 安装

### 1. 系统依赖（Linux）

```bash
sudo apt install xclip xdotool python3-pyaudio portaudio19-dev libnotify-bin
```

- `xclip` / `xdotool` 用于把识别文字粘贴到当前光标
- `portaudio` 是 PyAudio 的底层依赖
- `libnotify-bin` 用于桌面通知（可选）

### 2. Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 Groq API

```bash
cp .env.example .env
# 编辑 .env，填入 GROQ_API_KEY
```

免费 tier 申请：[console.groq.com/keys](https://console.groq.com/keys) （只需邮箱，无需信用卡）

每天 2000 次请求 / 每小时 7200 秒音频，远超个人副业用量。

### 4. 跑起来

```bash
export $(grep -v '^#' .env | xargs)  # 加载环境变量
python3 voice-input.py
```

按一下左 Alt → 说话 → 按 Enter 落字。

### 5. 开机自启（可选）

把上面命令包成 systemd user service 或 desktop autostart，省去每次手动启动。

---

## 工作机制

```
左 Alt 单键按下 → 启动录音线程
  → pyaudio 16kHz 采样
  → webrtcvad 每 30ms 帧判断说话/静音
  → 检测到 ≥0.4s 连续静音 = 一段语音结束
  → 异步把这段发 Groq whisper-large-v3
  → 拿到文字 → 过 is_hallucination + clean_text 双重过滤
  → 通过 xclip + xdotool 粘贴到当前光标
按 Enter 或再按左 Alt → 停止录音
```

关键参数（[voice-input.py:36-40](./voice-input.py#L36)）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `VAD_AGGRESSIVENESS` | 2 | 0-3，越高越不容易把噪音误判成语音 |
| `SILENCE_FRAMES` | 13 | 连续 13 帧（0.4s）静音 = 一段结束 |
| `MIN_SPEECH_FRAMES` | 34 | 短于 34 帧（1s）的语音段直接丢弃，过滤短脉冲 |
| `MAX_SPEECH_SECONDS` | 15 | 单段最长 15 秒，超了强制切 |

---

## 调参建议

- **太多噪音段被识别成 "music ♪"**：`VAD_AGGRESSIVENESS` 调到 3，或 `MIN_SPEECH_FRAMES` 提到 50+
- **说话太快被切碎**：`SILENCE_FRAMES` 调大到 17-20（但延迟会增加）
- **想加快出字**：`SILENCE_FRAMES` 调到 10（约 0.3s），代价是说话稍微停顿就被切
- **新增幻觉模式**：往 [`HALLUCINATION_PATTERNS`](./voice-input.py#L43) 数组里加，长度 ≥20 字单条命中也会被过滤

---

## FAQ

**Q：为什么不用本地 Whisper（whisper.cpp）？**
A：Groq 推理速度比本机快 10-20 倍，且免费 tier 完全够日常用。本地跑 whisper-large-v3 在中端 CPU 上要 2-4 秒/段，Groq 0.3-0.5 秒。

**Q：能识别中英文混合吗？**
A：可以。代码里 `prompt="以下是中英文混合的语音内容。"`（[voice-input.py:105](./voice-input.py#L105)）专门提示了模型，"打开 VS Code"「open VSCode」都识别得出。

**Q：Wayland 能用吗？**
A：当前用 `xdotool` + `xclip`（X11 工具），Wayland 需要换成 `wtype` + `wl-copy`，简单替换 [`type_text()`](./voice-input.py#L135) 里的命令即可。

**Q：Windows / macOS 能用吗？**
A：核心识别逻辑（pyaudio + webrtcvad + Groq）跨平台，但 [`type_text()`](./voice-input.py#L135) 和热键监听需要换：
- Windows：用 [pyautogui](https://pyautogui.readthedocs.io/) 或 AutoHotkey
- macOS：用 `osascript` 或 `pyobjc`

---

## 致谢

- [Groq](https://groq.com/) 提供超快推理 + 慷慨免费 tier
- [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) 把 Google WebRTC VAD 包成 Python 模块
- [Claude Code](https://claude.com/claude-code) 我没写过一行 Linux 系统编程，全程 AI 配合写出来的

> 「只有你想不到的，没有 Claude 做不到的。」

---

## License

MIT。随便用、随便改、随便发。
