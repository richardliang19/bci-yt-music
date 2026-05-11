"""
EEG 訊號來源（給 bci_server.py 與其他腳本共用）
─────────────────────────────────────────────────────────────
每個 Source 提供 read_new() → (np.ndarray, eof_bool)
回傳「自上次呼叫以來新累積的取樣點」。
"""

import os
import sys
import time
import threading
from collections import deque
import numpy as np


# ── Raw 眼動尖峰偵測（用來數眨眼次數）─────────────────────────────────────────
class RawBlinkDetector:
    """
    從 raw Fp1 訊號直接抓眨眼尖峰。

    為什麼需要這個？
      模型 (bci_model.pkl) 看 4 秒視窗，把「快速連續眨眼」整段看成「一個
      blink 狀態」，無法分辨「眨 1 下、2 下、3 下」。但眨眼在 Fp1 電極上
      會產生 200-500 µV 的明顯瞬間尖峰，用簡單的 threshold + refractory
      就能可靠數出每一下。

    演算法：
      1. 維護最近 N 秒的 buffer，估計 baseline = median，spread = 1.4826*MAD
         （MAD = Median Absolute Deviation，比 std 抗離群值）
      2. 偵測 |x - baseline| > threshold_mult * spread 的瞬間
      3. 兩個尖峰之間至少間隔 refractory_s（避免一次眨眼被算成多次）
    """
    def __init__(self, fs, threshold_mult=5.0, refractory_s=0.25,
                 baseline_window_s=3.0, min_amp=80.0):
        self.fs = fs
        self.threshold_mult = threshold_mult
        self.refractory_samples = int(refractory_s * fs)
        self.baseline_size = int(baseline_window_s * fs)
        self.min_amp = min_amp                  # 絕對下限，避免低訊號時誤觸
        self.buf = deque(maxlen=self.baseline_size)
        self.samples_since_peak = self.refractory_samples + 1
        self.total_samples = 0
        # 快取 baseline，每 0.25s 重算一次
        self._cached_baseline = 0.0
        self._cached_threshold = self.min_amp
        self._last_baseline_recalc = 0

    def _recompute_baseline(self):
        if len(self.buf) < self.fs // 2:
            return
        arr = np.fromiter(self.buf, dtype=np.float64)
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med))) * 1.4826
        self._cached_baseline = med
        self._cached_threshold = max(self.threshold_mult * mad, self.min_amp)

    def update(self, new_samples, now_t):
        """
        new_samples: 1D np.ndarray
        now_t     : 當下 wall time（end of chunk）
        return    : list[float]  新偵測到的尖峰時間戳（wall time 估計）
        """
        if len(new_samples) == 0:
            return []
        events = []
        n = len(new_samples)
        # 分批塞 buffer + 偵測
        for i, val in enumerate(new_samples):
            self.buf.append(float(val))
            self.samples_since_peak += 1
            self.total_samples += 1
            # 0.25s 重算一次 baseline
            if self.total_samples - self._last_baseline_recalc > self.fs // 4:
                self._recompute_baseline()
                self._last_baseline_recalc = self.total_samples
            # 偵測
            if (abs(val - self._cached_baseline) > self._cached_threshold
                    and self.samples_since_peak >= self.refractory_samples):
                # 估計這個尖峰的 wall time
                t = now_t - (n - i) / self.fs
                events.append(t)
                self.samples_since_peak = 0
        return events

    def get_state(self):
        return {
            "baseline": self._cached_baseline,
            "threshold": self._cached_threshold,
            "buf_filled": len(self.buf),
        }


# ── 檔案回放 ──────────────────────────────────────────────────────────────────
class FileReplay:
    """把單一 .txt 檔當成即時串流回放，模擬指定取樣率"""
    def __init__(self, path, fs):
        self.data = np.loadtxt(path).astype(np.float64)
        self.fs = fs
        self.idx = 0
        self.t0 = None  # 第一次 read_new() 才設，避免被模型載入時間吃掉
        print(f"[FileReplay] {path}（{len(self.data)} 點，{len(self.data)/fs:.1f} 秒）")

    def read_new(self):
        if self.t0 is None:
            self.t0 = time.time()
        elapsed = time.time() - self.t0
        target = min(int(elapsed * self.fs), len(self.data))
        new = self.data[self.idx:target]
        self.idx = target
        return new, (self.idx >= len(self.data))

    def close(self):
        pass


# ── 假資料 ───────────────────────────────────────────────────────────────────
class DummySource:
    """白雜訊 + 偶發尖峰，純粹測 pipeline"""
    def __init__(self, fs):
        self.fs = fs
        self.t0 = time.time()
        self.last_idx = 0
        self.rng = np.random.default_rng(0)
        print("[Dummy] 啟動")

    def read_new(self):
        target = int((time.time() - self.t0) * self.fs)
        n = target - self.last_idx
        if n <= 0:
            return np.empty(0), False
        sig = self.rng.normal(0, 30, n)
        if self.rng.random() < 0.02:
            sig[: min(64, n)] += 200
        self.last_idx = target
        return sig, False

    def close(self):
        pass


# ── BrainLink Lite via pyserial（NeuroSky TGAM 協定）──────────────────────────
class BrainLinkSerialSource:
    """
    BrainLink Lite 直連：用 pyserial 讀串流，自己解析 NeuroSky TGAM packet。

    為什麼不用 BrainFlow？
      BrainFlow 5.x 之後移除了 NEUROSKY_BOARD（AttributeError）。
      改用 pyserial 直接解 TGAM 協定 → 更穩定且少一個依賴。

    協定簡介（TGAM packet）：
      [0xAA 0xAA]  SYNC（兩個 0xAA）
      [PLENGTH]    payload 長度（0-169）
      [PAYLOAD]    PLENGTH 個 byte
      [CHKSUM]     payload 總和取低 8 bit 後反轉

    PAYLOAD 內每個 row：
      CODE < 0x80   → 後接 1 byte 資料
      CODE >= 0x80  → 後接 [VLENGTH][data x VLENGTH]

    CODE 0x80（length=2）= RAW_WAVE，16-bit signed big-endian，512 Hz
    這是我們唯一需要的東西。
    """
    SYNC = 0xAA
    CODE_RAW_WAVE = 0x80

    def __init__(self, port, fs=512, baud=57600):
        try:
            import serial
        except ImportError:
            sys.exit("缺少 pyserial：python -m pip install pyserial --user")
        self.fs = fs
        try:
            self.ser = serial.Serial(port, baudrate=baud, timeout=0.05)
        except Exception as e:
            sys.exit(f"開啟 {port} 失敗：{e}\n"
                     f"檢查項目：\n"
                     f"  1. BrainLink 已開機（藍燈閃）\n"
                     f"  2. Windows 藍牙設定已配對\n"
                     f"  3. 沒有其他程式佔用 {port}（如 brainlink.exe）")
        self.buf = []
        self.lock = threading.Lock()
        self.running = True
        self.bad_packets = 0
        self.good_packets = 0
        self.parser_thread = threading.Thread(target=self._parse_loop, daemon=True)
        self.parser_thread.start()
        print(f"[BrainLink] 已連線 {port} @ {baud} baud（背景 TGAM 解析中）")

    def _parse_loop(self):
        ser = self.ser
        while self.running:
            try:
                # 找 SYNC（0xAA 0xAA）
                b = ser.read(1)
                if not b or b[0] != self.SYNC:
                    continue
                b = ser.read(1)
                if not b or b[0] != self.SYNC:
                    continue
                # PLENGTH
                b = ser.read(1)
                if not b:
                    continue
                plen = b[0]
                if plen > 169:
                    self.bad_packets += 1
                    continue
                # PAYLOAD
                payload = ser.read(plen)
                if len(payload) != plen:
                    self.bad_packets += 1
                    continue
                # CHKSUM
                b = ser.read(1)
                if not b:
                    continue
                chksum = b[0]
                if (sum(payload) ^ 0xFF) & 0xFF != chksum:
                    self.bad_packets += 1
                    continue

                # 解析 payload 內的 CODE rows
                i = 0
                samples = []
                while i < len(payload):
                    code = payload[i]; i += 1
                    if code & 0x80:
                        if i >= len(payload):
                            break
                        length = payload[i]; i += 1
                        data = payload[i:i+length]; i += length
                        if code == self.CODE_RAW_WAVE and length == 2 and len(data) == 2:
                            val = int.from_bytes(data, 'big', signed=True)
                            samples.append(float(val))
                    else:
                        i += 1  # 1-byte data，略過

                self.good_packets += 1
                if samples:
                    with self.lock:
                        self.buf.extend(samples)
            except Exception as e:
                if self.running:
                    print(f"[BrainLink] 解析例外：{e}", flush=True)

    def read_new(self):
        with self.lock:
            if not self.buf:
                return np.empty(0), False
            data = np.array(self.buf, dtype=np.float64)
            self.buf.clear()
        return data, False

    def stats(self):
        return {"good_packets": self.good_packets, "bad_packets": self.bad_packets}

    def close(self):
        self.running = False
        try:
            self.parser_thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass


# ── 資料夾監看（brainlink.exe 寫檔模式） ─────────────────────────────────────
class FolderWatcher:
    """
    監看資料夾，每出現新 .txt 就把整檔當成「新一批訊號」吐出去。
    適用 brainlink.exe 每 20 秒寫一個檔的工作流程。
    """
    def __init__(self, folder, fs):
        self.folder = folder
        self.fs = fs
        self.seen = set(os.listdir(folder)) if os.path.isdir(folder) else set()
        if not os.path.isdir(folder):
            sys.exit(f"[FolderWatcher] 資料夾不存在：{folder}")
        print(f"[FolderWatcher] 監看 {folder}（已忽略 {len(self.seen)} 個既有檔）")

    def read_new(self):
        try:
            current = set(os.listdir(self.folder))
        except OSError:
            return np.empty(0), False
        new_files = sorted(current - self.seen)
        self.seen = current
        if not new_files:
            return np.empty(0), False
        chunks = []
        for fn in new_files:
            if not fn.lower().endswith(".txt"):
                continue
            try:
                d = np.loadtxt(os.path.join(self.folder, fn)).astype(np.float64)
                chunks.append(d)
                print(f"[FolderWatcher] 新檔：{fn}（{len(d)} 點）")
            except Exception as e:
                print(f"[FolderWatcher] 讀取失敗 {fn}: {e}")
        if not chunks:
            return np.empty(0), False
        return np.concatenate(chunks), False

    def close(self):
        pass


# ── 工廠函式 ──────────────────────────────────────────────────────────────────
def make_source(arg, fs):
    """
    根據字串建立訊號來源：
      "dummy"            → DummySource
      "brainlink:COM3"   → BrainLinkSerialSource（pyserial + TGAM）
      "brainflow:COM3"   → 同上（向後相容別名，舊指令還能跑）
      "file:path.txt"    → FileReplay
      "folder:path/"     → FolderWatcher
    """
    if arg == "dummy":
        return DummySource(fs)
    if arg.startswith("brainlink") or arg.startswith("brainflow"):
        if ":" in arg:
            port = arg.split(":", 1)[1].strip()
        else:
            port = os.environ.get("BRAINLINK_PORT") or input("輸入 COM port（如 COM3）：").strip()
        return BrainLinkSerialSource(port, fs)
    if arg.startswith("file:"):
        return FileReplay(arg[5:], fs)
    if arg.startswith("folder:"):
        return FolderWatcher(arg[7:], fs)
    sys.exit(f"未知 --source：{arg}")
