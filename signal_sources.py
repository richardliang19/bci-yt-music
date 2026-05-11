"""
EEG 訊號來源（給 bci_server.py 與 realtime_classify.py 共用）
─────────────────────────────────────────────────────────────
每個 Source 提供 read_new() → (np.ndarray, eof_bool)
回傳「自上次呼叫以來新累積的取樣點」。
"""

import os
import sys
import time
import numpy as np


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


# ── BrainLink Lite via BrainFlow ──────────────────────────────────────────────
class BrainFlowSource:
    """BrainLink Lite（NeuroSky TGAM 相容板）"""
    def __init__(self, serial_port, fs):
        try:
            from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
        except ImportError:
            sys.exit(
                "未安裝 brainflow，請先執行：\n"
                "  python -m pip install brainflow --user"
            )
        params = BrainFlowInputParams()
        params.serial_port = serial_port
        # NeuroSky board id = 2，BrainLink 系列的 TGAM 模組共用此驅動
        self.board = BoardShim(BoardIds.NEUROSKY_BOARD.value, params)
        self.eeg_ch = BoardShim.get_eeg_channels(BoardIds.NEUROSKY_BOARD.value)[0]
        self.board.prepare_session()
        self.board.start_stream()
        self.fs = fs
        print(f"[BrainFlow] BrainLink 已連線（{serial_port}）")

    def read_new(self):
        data = self.board.get_board_data()  # n_channels x n_samples
        if data.size == 0:
            return np.empty(0), False
        return data[self.eeg_ch].astype(np.float64), False

    def close(self):
        try:
            self.board.stop_stream()
            self.board.release_session()
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
      "brainflow"        → BrainFlowSource（會問 COM port）
      "brainflow:COM3"   → BrainFlowSource（指定 port）
      "file:path.txt"    → FileReplay
      "folder:path/"     → FolderWatcher
    """
    if arg == "dummy":
        return DummySource(fs)
    if arg.startswith("brainflow"):
        if ":" in arg:
            port = arg.split(":", 1)[1].strip()
        else:
            port = os.environ.get("BRAINLINK_PORT") or input("輸入 COM port（如 COM3）：").strip()
        return BrainFlowSource(port, fs)
    if arg.startswith("file:"):
        return FileReplay(arg[5:], fs)
    if arg.startswith("folder:"):
        return FolderWatcher(arg[7:], fs)
    sys.exit(f"未知 --source：{arg}")
