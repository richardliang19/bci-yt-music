"""
全資料集品質診斷（更新版資料集，18 受試者）
對每位受試者計算：
  - 三任務頻帶分布差異（可分性指標）
  - 訊號 std / 峰峰值（是否失真）
flag 出該排除的受試者。
"""
import numpy as np
import os, glob

DATA = "bci_dataset_114-2_update/bci_dataset_114-2_any"
LABELS = {1: "Relax", 2: "Focus", 3: "Blink"}
BANDS = {"Delta": (1, 4), "Theta": (4, 8), "Alpha": (8, 13),
         "Beta": (13, 30), "Gamma": (30, 40)}
FS = 512


def band_rel_power(folder, task):
    files = sorted(glob.glob(os.path.join(folder, f"*_{task}_*.txt")))
    powers = {b: [] for b in BANDS}
    stds, p2ps = [], []
    for fp in files:
        try:
            d = np.loadtxt(fp)
        except Exception:
            continue
        stds.append(np.std(d))
        p2ps.append(d.max() - d.min())
        d = d - np.mean(d)
        seg_len = 4 * FS
        for s in range(0, len(d) - seg_len + 1, seg_len):
            seg = d[s:s+seg_len] * np.hanning(seg_len)
            freqs = np.fft.rfftfreq(seg_len, 1/FS)
            psd = np.abs(np.fft.rfft(seg))**2
            total = psd[(freqs >= 1) & (freqs <= 40)].sum()
            if total < 1e-12:
                continue
            for name, (lo, hi) in BANDS.items():
                powers[name].append(psd[(freqs >= lo) & (freqs < hi)].sum() / total)
    rel = {b: (float(np.mean(v)) if v else 0.0) for b, v in powers.items()}
    return rel, (float(np.mean(stds)) if stds else 0), (float(np.mean(p2ps)) if p2ps else 0)


def separability(task_bands):
    """三任務頻帶向量兩兩 L1 距離總和；越大代表越可分"""
    vecs = []
    for t in [1, 2, 3]:
        vecs.append(np.array([task_bands[t][b] for b in BANDS]))
    d = (np.abs(vecs[0]-vecs[1]).sum() +
         np.abs(vecs[0]-vecs[2]).sum() +
         np.abs(vecs[1]-vecs[2]).sum())
    return d


print("=" * 78)
print("更新版資料集 — 全受試者品質診斷")
print("=" * 78)
print(f"{'受試者':<6} {'可分性':>8} {'平均std':>9} {'平均p2p':>9}  判斷")
print("-" * 78)

subjects = sorted([d for d in os.listdir(DATA) if os.path.isdir(os.path.join(DATA, d))])
flags = {}
for sub in subjects:
    folder = os.path.join(DATA, sub)
    task_bands, stds, p2ps = {}, [], []
    for t in [1, 2, 3]:
        rel, std, p2p = band_rel_power(folder, t)
        task_bands[t] = rel
        stds.append(std)
        p2ps.append(p2p)
    sep = separability(task_bands)
    mean_std = np.mean(stds)
    mean_p2p = np.mean(p2ps)

    notes = []
    if sep < 0.15:
        notes.append("三任務太相似(不可分)")
    if mean_std > 150:
        notes.append("std過大(失真)")
    if mean_p2p > 1800:
        notes.append("p2p過大(飽和)")
    verdict = "⚠ " + " / ".join(notes) if notes else "OK"
    flags[sub] = bool(notes)
    print(f"{sub:<6} {sep:>8.3f} {mean_std:>9.1f} {mean_p2p:>9.0f}  {verdict}")

print("-" * 78)
bad = [s for s, f in flags.items() if f]
print(f"建議排除：{bad if bad else '（無，全部可用）'}")
print("=" * 78)
