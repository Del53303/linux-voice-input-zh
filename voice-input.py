#!/usr/bin/env python3
"""
语音输入工具 - WebRTC VAD + 分段发送版
按一下左 Alt 开始录音；再按一下左 Alt 停止；或在录音时按 Enter 自动停止并发送

工作原理：
- 用 webrtcvad（Google 的语音检测算法）检测说话/停顿
- 说话时收集音频，停顿时立刻发送
- 每段只发新音频，直接追加，不做 diff
- 左 Alt 单独按下并释放（期间没碰别的键）才算触发，所以 Alt+Tab 等组合不会误触发
"""

import subprocess
import tempfile
import wave
import time
import sys
import os
import struct
import pyaudio
import threading
import webrtcvad
from collections import deque
from pynput import keyboard

API_KEY = os.environ.get("GROQ_API_KEY", "")

# 音频参数（webrtcvad 要求 frame 为 10/20/30ms）
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # paInt16
FRAME_DURATION_MS = 30  # 每帧 30ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 480 samples
MAX_SECONDS = 300

# VAD 参数
VAD_AGGRESSIVENESS = 2    # 0-3，越高越激进（越不容易误判噪音为语音）
SILENCE_FRAMES = 13       # 连续这么多帧静音 = 一句话说完（13帧 × 30ms ≈ 0.4秒）
MIN_SPEECH_FRAMES = 34    # 最短语音帧数（34帧 × 30ms ≈ 1秒，过滤噪音短脉冲）
MAX_SPEECH_SECONDS = 15   # 连续说话超过这么久强制切

# Whisper 幻觉黑名单
HALLUCINATION_PATTERNS = [
    "请不吝点赞", "订阅", "转发", "打赏", "明镜", "点点栏目",
    "amara.org", "字幕", "志愿者", "社群提供",
    "感谢观看", "谢谢观看", "谢谢大家", "下期再见", "拜拜",
    "thank you", "thanks for watching", "subscribe",
    "please like", "see you next",
    "字幕由", "字幕制作", "字幕校对",
    "music", "♪", "♫", "中文",
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
]

is_recording = False
stop_event = threading.Event()
rec_thread = None
target_window = [None]


NOTIFY_ID = 99999

def notify(msg, timeout_ms=3000):
    subprocess.Popen([
        "notify-send", "-t", str(timeout_ms),
        "-r", str(NOTIFY_ID),
        "-i", "audio-input-microphone",
        "语音输入", msg
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(msg)


def is_hallucination(text):
    t = text.lower().strip()
    if not t:
        return True
    matches = sum(1 for p in HALLUCINATION_PATTERNS if p.lower() in t)
    if len(t) < 20 and matches >= 1:
        return True
    if matches >= 2:
        return True
    return False


def clean_text(text):
    changed = True
    while changed:
        changed = False
        t = text.rstrip()
        for pat in HALLUCINATION_PATTERNS:
            if t.lower().endswith(pat.lower()):
                t = t[:len(t) - len(pat)].rstrip()
                changed = True
        text = t
    return text


def transcribe(audio_file):
    from groq import Groq
    client = Groq(api_key=API_KEY, timeout=15.0)
    with open(audio_file, "rb") as f:
        result = client.audio.transcriptions.create(
            file=("audio.wav", f.read()),
            model="whisper-large-v3",
            language="zh",
            prompt="以下是中英文混合的语音内容。",
            response_format="text",
        )
    return result.strip()


def save_wav(frames):
    wav_path = tempfile.mktemp(suffix=".wav")
    wf = wave.open(wav_path, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(b''.join(frames))
    wf.close()
    return wav_path


def is_terminal_window():
    try:
        win_id = subprocess.run(["xdotool", "getactivewindow"],
                                capture_output=True, text=True).stdout.strip()
        result = subprocess.run(["xprop", "-id", win_id, "WM_CLASS"],
                                capture_output=True, text=True)
        wm_class = result.stdout.strip().lower()
        terminals = ["terminal", "xterm", "konsole", "kitty", "alacritty", "tilix"]
        return any(t in wm_class for t in terminals)
    except:
        return False


def type_text(text, window_id):
    if not text:
        return
    if window_id:
        subprocess.run(["xdotool", "windowactivate", "--sync", window_id],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.02)
    subprocess.run(["xclip", "-selection", "clipboard"],
                   input=text.encode("utf-8"), check=False)
    time.sleep(0.02)
    if is_terminal_window():
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"])
    else:
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"])


def get_active_window():
    try:
        return subprocess.run(["xdotool", "getactivewindow"],
                              capture_output=True, text=True).stdout.strip()
    except:
        return None


def do_record_and_stream():
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=SAMPLE_RATE,
                    input=True, frames_per_buffer=FRAME_SIZE)
    stop_event.clear()

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

    frame_duration = FRAME_DURATION_MS / 1000.0
    max_speech_frames = int(MAX_SPEECH_SECONDS / frame_duration)
    max_total_frames = int(MAX_SECONDS / frame_duration)

    win_id = target_window[0]

    # VAD 状态
    speech_frames = []
    silent_count = 0
    in_speech = False
    total_frames = 0

    # 转写队列
    transcribe_queue = deque()
    transcribe_lock = threading.Lock()

    def transcribe_worker():
        while True:
            item = None
            with transcribe_lock:
                if transcribe_queue:
                    item = transcribe_queue.popleft()

            if item is None:
                if stop_event.is_set():
                    with transcribe_lock:
                        if not transcribe_queue:
                            break
                time.sleep(0.03)
                continue

            try:
                duration = len(item) * frame_duration
                print(f"  转写 {duration:.1f}s 片段...")
                wav_path = save_wav(item)
                text = transcribe(wav_path)
                os.unlink(wav_path)
                print(f"  结果: {text}")

                text = clean_text(text)
                if text and text.strip() and not is_hallucination(text):
                    type_text(text, win_id)
                    print(f"  输出: {text}")
                elif text:
                    print(f"  已过滤: {text}")
            except Exception as e:
                print(f"转写出错: {e}")

    worker = threading.Thread(target=transcribe_worker, daemon=True)
    worker.start()

    while not stop_event.is_set() and total_frames < max_total_frames:
        data = stream.read(FRAME_SIZE, exception_on_overflow=False)
        total_frames += 1

        # webrtcvad 判断这一帧是否有语音
        is_speech = vad.is_speech(data, SAMPLE_RATE)

        if is_speech:
            if not in_speech:
                in_speech = True
                print(f"  语音开始")
            speech_frames.append(data)
            silent_count = 0

            # 说太久了，强制切
            if len(speech_frames) >= max_speech_frames:
                print(f"  连续说话 {MAX_SPEECH_SECONDS}s，强制切段")
                with transcribe_lock:
                    transcribe_queue.append(speech_frames[:])
                speech_frames.clear()
        else:
            if in_speech:
                speech_frames.append(data)  # 保留静音尾巴
                silent_count += 1

                if silent_count >= SILENCE_FRAMES:
                    if len(speech_frames) >= MIN_SPEECH_FRAMES:
                        duration = len(speech_frames) * frame_duration
                        print(f"  语音结束，{duration:.1f}s，发送")
                        with transcribe_lock:
                            transcribe_queue.append(speech_frames[:])
                    else:
                        print(f"  语音太短，丢弃")

                    speech_frames.clear()
                    silent_count = 0
                    in_speech = False

    stream.stop_stream()
    stream.close()

    if speech_frames and len(speech_frames) >= MIN_SPEECH_FRAMES:
        with transcribe_lock:
            transcribe_queue.append(speech_frames[:])

    stop_event.set()
    worker.join(timeout=30)

    p.terminate()
    notify("✅ 转写完成", timeout_ms=2000)


def start_recording():
    global is_recording, rec_thread
    target_window[0] = get_active_window()
    is_recording = True
    stop_event.clear()
    rec_thread = threading.Thread(target=do_record_and_stream)
    rec_thread.start()
    notify("🎤 录音中...", timeout_ms=300000)


def stop_recording():
    global is_recording, rec_thread
    stop_event.set()
    if rec_thread:
        rec_thread.join()
    is_recording = False


def on_hotkey():
    if is_recording:
        stop_recording()
    else:
        start_recording()


def main():
    if subprocess.run(["which", "xclip"], capture_output=True).returncode != 0:
        print("需要安装 xclip: sudo apt install xclip")
        sys.exit(1)

    print("=== 语音输入工具（WebRTC VAD 版）===")
    print("快捷键: 左 Alt（单键）")
    print("  第一次按 = 开始录音")
    print("  第二次按 = 停止录音")
    print("  录音中按 Enter = 停止录音（发送消息的同时自动停）")
    print(f"  VAD 灵敏度: {VAD_AGGRESSIVENESS} (0-3)")
    print(f"  停顿检测: {SILENCE_FRAMES * FRAME_DURATION_MS}ms")
    print("  左 Alt 单独按下并释放时才触发；配合其他键（Alt+Tab 等）不会误触发")
    print("Ctrl+C = 退出")
    print()

    # 左 Alt 单键触发的状态机：
    # - 按下左 Alt：alt_solo = True
    # - 按下任何其他键：alt_solo = False（破坏单键条件）
    # - 释放左 Alt 时：alt_solo 为 True 才触发 on_hotkey
    alt_solo = [False]

    def on_press(key):
        if key == keyboard.Key.alt_l:
            alt_solo[0] = True
        else:
            alt_solo[0] = False

        # 录音中按 Enter 自动停止录音
        if is_recording and key == keyboard.Key.enter:
            threading.Thread(target=stop_recording, daemon=True).start()

    def on_release(key):
        if key == keyboard.Key.alt_l:
            if alt_solo[0]:
                on_hotkey()
            alt_solo[0] = False

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            stop_event.set()
            print("\n退出")


if __name__ == "__main__":
    main()
