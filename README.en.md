# Chinese Voice Input for Linux · 351 lines, single file

> Speak Chinese into any Linux app. Press left Alt, talk, press Enter — text lands at the cursor.
>
> Powered by Groq's free tier running whisper-large-v3. **€0 cost.**
>
> [中文 README](./README.md)

---

## What is this

A 351-line single-file Python tool that converts Chinese speech to text and types it directly at your cursor.

**Use case**: French / Spanish / German keyboards have no native pinyin input. Typing 30 Chinese characters takes over a minute. With this tool, the same sentence takes 1-2 seconds.

**Linux has no equivalent of Windows' Win+H built-in voice input for Chinese**, so I built one.

---

## 3 real pain points + 1 surprise

> Honest dev log. If you're building your own Chinese voice input, copy these solutions.

### ① Whisper hallucination: silence transcribed as YouTube subtitles

First run: silent mic, screen filled with "请大家点赞订阅" (please like and subscribe), "字幕志愿者" (subtitle volunteers), "music ♪". Groq's whisper-large-v3 was trained on lots of YouTube subtitles, so silence triggers hallucination.

**Fix**: 17-pattern blacklist ([`HALLUCINATION_PATTERNS`](./voice-input.py#L43)) + dual filtering [`is_hallucination()`](./voice-input.py#L72) + [`clean_text()`](./voice-input.py#L84).

### ② Latency: had to finish a whole paragraph before any text appeared

v1 sent the entire recording to Groq only after stopping. A 30-second paragraph meant 5-10 seconds of waiting before any text. Watching a blank screen while talking — exhausting.

**Fix**: [webrtcvad](https://github.com/wiseman/py-webrtcvad) (Google's open-source WebRTC voice activity detector) splits on natural pauses (~400ms silence). Each segment goes to Groq independently and async, text appears immediately. Recording-then-output became speak-and-see. See [`do_record_and_stream()`](./voice-input.py#L159).

### ③ Hotkey fatigue: 3-key combo, both hands

Original `Ctrl+Alt+V`: left hand on Ctrl+Alt, right hand on V. After 50+ uses a day, my left wrist hurt.

**Fix**: Single left-Alt key + a hand-rolled state machine. If any other key is pressed while left Alt is held down, mark dirty. On release, only trigger if dirty=False. So `Alt+Tab` `Alt+F4` etc. don't false-trigger. See [`on_press / on_release`](./voice-input.py#L317).

### 💡 The surprise

Of the 3 pain points, I assumed system-level hotkey rebinding would be hardest (aren't shortcuts hardcoded in the OS?). It turned out simplest — a listener and state machine. The "trivial-looking" Whisper output hid 17 subtitle-volunteer ghosts.

**Intuition about difficulty often runs opposite to actual difficulty.**

---

## Install

### 1. System dependencies (Linux)

```bash
sudo apt install xclip xdotool python3-pyaudio portaudio19-dev libnotify-bin
```

- `xclip` / `xdotool`: paste recognized text at cursor
- `portaudio`: PyAudio backend
- `libnotify-bin`: desktop notifications (optional)

### 2. Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Groq API

```bash
cp .env.example .env
# Edit .env, fill in GROQ_API_KEY
```

Get a free key at [console.groq.com/keys](https://console.groq.com/keys) (email only, no credit card).

Free tier: 2000 requests/day, 7200 audio seconds/hour — way more than personal use.

### 4. Run

```bash
export $(grep -v '^#' .env | xargs)  # load env vars
python3 voice-input.py
```

Press left Alt → talk → press Enter to commit.

### 5. Auto-start (optional)

Wrap the command in a systemd user service or desktop autostart entry.

---

## How it works

```
Single left-Alt press → start recording thread
  → pyaudio captures 16kHz audio
  → webrtcvad checks each 30ms frame for speech/silence
  → ≥0.4s of continuous silence = end of utterance
  → async send segment to Groq whisper-large-v3
  → result → is_hallucination + clean_text dual filter
  → paste at cursor via xclip + xdotool
Press Enter or left Alt again → stop recording
```

Key parameters ([voice-input.py:36-40](./voice-input.py#L36)):

| Param | Default | Meaning |
|---|---|---|
| `VAD_AGGRESSIVENESS` | 2 | 0-3, higher = less false speech detection |
| `SILENCE_FRAMES` | 13 | 13 frames (0.4s) silent = utterance end |
| `MIN_SPEECH_FRAMES` | 34 | <34 frames (1s) speech segments dropped |
| `MAX_SPEECH_SECONDS` | 15 | Max single segment, force-cut beyond |

---

## Tuning tips

- **Background noise transcribed as "music ♪"**: Set `VAD_AGGRESSIVENESS` to 3, raise `MIN_SPEECH_FRAMES` to 50+
- **Fast speech being chopped up**: Raise `SILENCE_FRAMES` to 17-20 (latency increases)
- **Want faster output**: Lower `SILENCE_FRAMES` to 10 (~0.3s), at the cost of more aggressive segmentation
- **New hallucination patterns**: Add to [`HALLUCINATION_PATTERNS`](./voice-input.py#L43); single match in <20-char output triggers filter

---

## FAQ

**Q: Why not local Whisper (whisper.cpp)?**
A: Groq is 10-20x faster than local inference, and the free tier covers daily use. Local whisper-large-v3 takes 2-4s per segment on a mid-range CPU; Groq does 0.3-0.5s.

**Q: Mixed Chinese-English?**
A: Yes. The code uses `prompt="以下是中英文混合的语音内容。"` ([voice-input.py:105](./voice-input.py#L105)) to hint the model. "打开 VS Code" / "open VSCode" both work.

**Q: Wayland support?**
A: Currently uses `xdotool` + `xclip` (X11). For Wayland, swap them for `wtype` + `wl-copy` in [`type_text()`](./voice-input.py#L135).

**Q: Windows / macOS?**
A: Core recognition (pyaudio + webrtcvad + Groq) is cross-platform, but [`type_text()`](./voice-input.py#L135) and hotkey listening need replacing:
- Windows: [pyautogui](https://pyautogui.readthedocs.io/) or AutoHotkey
- macOS: `osascript` or `pyobjc`

---

## Credits

- [Groq](https://groq.com/) for blazing-fast inference and a generous free tier
- [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) for wrapping Google's WebRTC VAD
- [Claude Code](https://claude.com/claude-code) — I've never written a line of Linux system programming. This was AI-paired the whole way.

> "There's nothing Claude can't do — only things you didn't think to ask."

---

## License

MIT. Use, modify, redistribute freely.
