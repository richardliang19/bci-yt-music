"""
診斷工具：比較 S08/S10（差）vs S09/S15（好）的訊號特性
找出為什麼某些受試者準確率特別低
"""
import numpy as np
import os, glob

DATA = "bci_dataset_114-2"
TARGETS = ["S08", "S09", "S10", "S15"]
LABELS = {1: "Relax", 2: "Focus", 3: "Blink"}

def signal_stats(folder):
    """計算每個任務類別的訊號特性"""
    out = {}
    for task in [1, 2, 3]:
        files = sorted(glob.glob(os.path.join(folder, f"*_{task}_*.txt")))
        all_data = []
        all_p2p = []      # 峰峰值
        all_std = []      # 標準差
        all_sat = []      # 飽和率（值卡在邊界的比例）
        all_zero = []     # 訊號為 0 的比例（可能掉線）
        for fp in files:
            try:
                d = np.loadtxt(fp)
                all_data.append(d)
                all_p2p.append(d.max() - d.min())
                all_std.append(np.std(d))
                # 飽和檢測：值是否大量集中在最大或最小值
                vmax, vmin = d.max(), d.min()
                sat = (np.sum(d == vmax) + np.sum(d == vmin)) / len(d)
                all_sat.append(sat)
                # 零值或極小值
                all_zero.append(np.sum(np.abs(d) < 1) / len(d))
            except:
                pass
        if all_data:
            full = np.concatenate(all_data)
            out[task] = {
                "n_files": len(files),
                "mean": float(np.mean(full)),
                "median": float(np.median(full)),
                "std": float(np.std(full)),
                "min": float(full.min()),
                "max": float(full.max()),
                "p2p_per_file_mean": float(np.mean(all_p2p)),
                "saturation_rate": float(np.mean(all_sat)),
                "near_zero_rate": float(np.mean(all_zero)),
            }
    return out

def freq_analysis(folder, fs=512):
    """各頻帶相對功率"""
    bands = {"Delta(1-4)":(1,4), "Theta(4-8)":(4,8),
             "Alpha(8-13)":(8,13), "Beta(13-30)":(13,30), "Gamma(30-40)":(30,40)}
    result = {}
    for task in [1, 2, 3]:
        files = sorted(glob.glob(os.path.join(folder, f"*_{task}_*.txt")))
        powers = {b: [] for b in bands}
        for fp in files:
            try:
                d = np.loadtxt(fp) - np.mean(np.loadtxt(fp))
                # 切 4 秒段
                seg_len = 4 * fs
                for s in range(0, len(d) - seg_len + 1, seg_len):
                    seg = d[s:s+seg_len] * np.hanning(seg_len)
                    freqs = np.fft.rfftfreq(seg_len, 1/fs)
                    psd = np.abs(np.fft.rfft(seg))**2
                    total = psd[(freqs>=1)&(freqs<=40)].sum()
                    if total < 1e-12: continue
                    for name, (lo, hi) in bands.items():
                        p = psd[(freqs>=lo)&(freqs<hi)].sum()
                        powers[name].append(p / total)
            except:
                pass
        result[task] = {b: float(np.mean(v)) if v else 0 for b, v in powers.items()}
    return result


print("="*70)
print("受試者訊號診斷 — 比較好/壞的差異")
print("="*70)

for sub in TARGETS:
    folder = os.path.join(DATA, sub)
    print(f"\n{'='*70}")
    print(f"▶ {sub}（之前 LOSO 結果：S08=0.33, S09=0.99, S10=0.15, S15=0.99）")
    print(f"{'='*70}")

    stats = signal_stats(folder)
    print(f"\n[基本訊號統計]")
    print(f"{'任務':<8} {'檔數':>4} {'均值':>8} {'中位數':>8} {'標準差':>8} {'峰峰值':>9} {'飽和率':>8} {'近零率':>8}")
    for task, s in stats.items():
        print(f"{LABELS[task]:<8} {s['n_files']:>4} {s['mean']:>8.1f} {s['median']:>8.1f} "
              f"{s['std']:>8.1f} {s['p2p_per_file_mean']:>9.1f} "
              f"{s['saturation_rate']*100:>7.2f}% {s['near_zero_rate']*100:>7.2f}%")

    freqs = freq_analysis(folder)
    print(f"\n[各頻帶相對功率%]")
    print(f"{'任務':<8} {'Delta':>10} {'Theta':>10} {'Alpha':>10} {'Beta':>10} {'Gamma':>10}")
    for task, f in freqs.items():
        print(f"{LABELS[task]:<8}", end="")
        for b in ["Delta(1-4)","Theta(4-8)","Alpha(8-13)","Beta(13-30)","Gamma(30-40)"]:
            print(f" {f[b]*100:>9.1f}%", end="")
        print()

print("\n" + "="*70)
print("診斷重點：")
print("  - 飽和率高 = 訊號常卡在邊界（電極接觸差或 BrainLink 飽和）")
print("  - 近零率高 = 大量資料為 0（可能藍牙掉線或感測器斷線）")
print("  - 三個任務的頻帶分布若幾乎相同 = 三類根本沒有可分性")
print("="*70)
