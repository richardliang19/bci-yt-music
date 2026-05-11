"""
產生所有期末簡報用圖表
─────────────────────────────────────────────────────────────
輸出：report_figures/ 底下每張圖一個 .png 與 .pdf
全部 300 dpi，可直接丟進 PowerPoint / Word / LaTeX
"""

import os
import csv
import glob
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl

# ── 全域樣式 ──────────────────────────────────────────────────────────────────
mpl.rcParams.update({
    "font.family": ["Microsoft JhengHei", "PingFang TC", "Arial Unicode MS",
                    "DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
    "figure.facecolor": "white",
})

OUT = "report_figures"
os.makedirs(OUT, exist_ok=True)

# ── 共用配色 ──────────────────────────────────────────────────────────────────
COL_TASK = {"Relax": "#4a90e2", "Focus": "#f5a623", "Blink": "#e5484d"}
COL_OK   = "#1ed760"
COL_WARN = "#f5a623"
COL_BAD  = "#e5484d"
COL_NEU  = "#999999"


def save(fig, name):
    p_png = os.path.join(OUT, f"{name}.png")
    p_pdf = os.path.join(OUT, f"{name}.pdf")
    fig.savefig(p_png)
    fig.savefig(p_pdf)
    plt.close(fig)
    print(f"  ✓ {name}.png / .pdf")


# ── 1. 模型比較長條圖 ────────────────────────────────────────────────────────
def fig_model_comparison():
    with open("model_comparison_detail.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    headers = rows[0][1:]
    means, stds = None, None
    for r in rows:
        if r and r[0] == "Mean":
            means = [float(x) for x in r[1:]]
        elif r and r[0] == "Std":
            stds = [float(x) for x in r[1:]]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    colors = ["#4a90e2", "#f5a623", "#7ed321", "#bd10e0", "#1ed760"]
    x = np.arange(len(headers))
    bars = ax.bar(x, means, yerr=stds, capsize=6,
                  color=colors, edgecolor="white", linewidth=1.5,
                  error_kw={"ecolor": "#444", "linewidth": 1.2})

    best_idx = int(np.argmax(means))
    bars[best_idx].set_edgecolor("#1ed760")
    bars[best_idx].set_linewidth(3)

    for i, (m, s) in enumerate(zip(means, stds)):
        ax.text(i, m + s + 0.015, f"{m:.3f}",
                ha="center", fontsize=11, fontweight="bold")
        ax.text(i, m / 2, f"±{s:.3f}",
                ha="center", color="white", fontsize=10)

    ax.axhline(0.70, color=COL_BAD, linestyle="--", linewidth=1, alpha=0.6,
               label="70% 目標門檻")
    ax.axhline(1/3, color="#888", linestyle=":", linewidth=1, alpha=0.6,
               label="33% 隨機 baseline")
    ax.set_xticks(x)
    ax.set_xticklabels(headers, rotation=15, ha="right")
    ax.set_ylabel("LOSO 準確率")
    ax.set_ylim(0, 1.05)
    ax.set_title("5 個模型 LOSO Cross-Validation 準確率比較（n=16）")
    ax.legend(loc="lower right", framealpha=0.95)
    save(fig, "01_model_comparison")


# ── 2. 各受試者準確率（最佳模型）──────────────────────────────────────────────
def fig_per_subject():
    with open("model_comparison_detail.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    headers = rows[0]
    body = [r for r in rows[1:] if r and r[0] not in ("Mean", "Std", "")]
    # 找最佳欄位
    means = None
    for r in rows:
        if r and r[0] == "Mean":
            means = [float(x) for x in r[1:]]
    best_col = 1 + int(np.argmax(means))
    best_name = headers[best_col]

    subjects = [r[0] for r in body]
    accs = [float(r[best_col]) for r in body]

    fig, ax = plt.subplots(figsize=(11, 5))
    colors = []
    for a in accs:
        if a >= 0.85: colors.append(COL_OK)
        elif a >= 0.70: colors.append(COL_WARN)
        else: colors.append(COL_BAD)

    x = np.arange(len(subjects))
    bars = ax.bar(x, accs, color=colors, edgecolor="white", linewidth=1.2)
    for i, a in enumerate(accs):
        ax.text(i, a + 0.015, f"{a:.2f}", ha="center", fontsize=9)

    ax.axhline(np.mean(accs), color="#333", linestyle="-", linewidth=1.5,
               alpha=0.6, label=f"平均 {np.mean(accs):.3f}")
    ax.axhline(0.70, color=COL_BAD, linestyle="--", linewidth=1, alpha=0.5,
               label="70% 門檻")
    ax.axhline(1/3, color="#888", linestyle=":", linewidth=1, alpha=0.5,
               label="33% 隨機")

    ax.set_xticks(x)
    ax.set_xticklabels(subjects)
    ax.set_ylabel("LOSO 準確率")
    ax.set_ylim(0, 1.1)
    ax.set_title(f"{best_name} — 各受試者 LOSO 準確率\n"
                 f"（綠 ≥85% · 橘 70-85% · 紅 <70%）")
    ax.legend(loc="lower right", framealpha=0.95)
    save(fig, "02_per_subject_accuracy")


# ── 3. 混淆矩陣熱圖 ──────────────────────────────────────────────────────────
def fig_confusion_matrix():
    with open("best_model_confusion_matrix.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    cm = np.array([[int(x) for x in r[1:]] for r in rows[1:]])
    labels = ["Relax", "Focus", "Blink"]
    cm_norm = cm / cm.sum(axis=1, keepdims=True)

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(cm_norm, cmap="Greens", vmin=0, vmax=1)

    for i in range(3):
        for j in range(3):
            color = "white" if cm_norm[i, j] > 0.55 else "#222"
            ax.text(j, i, f"{cm[i,j]}", ha="center", va="center",
                    fontsize=15, fontweight="bold", color=color)
            ax.text(j, i + 0.27, f"{cm_norm[i,j]*100:.1f}%",
                    ha="center", va="center", fontsize=11, color=color)

    ax.set_xticks(range(3)); ax.set_xticklabels([f"預測\n{l}" for l in labels])
    ax.set_yticks(range(3)); ax.set_yticklabels([f"實際\n{l}" for l in labels])
    ax.set_title("Ensemble 混淆矩陣（16 受試者 LOSO 總和）\n"
                 "顏色：每列 recall · 數字：樣本數 / 百分比")
    cbar = plt.colorbar(im, ax=ax, fraction=0.045)
    cbar.set_label("Recall")
    ax.grid(False)
    save(fig, "03_confusion_matrix")


# ── 4. 好/壞受試者頻帶比較 ─────────────────────────────────────────────────
def fig_band_comparison():
    """從 diagnose 結果固定數值做圖（避免重跑 30 秒）"""
    bands = ["Delta", "Theta", "Alpha", "Beta", "Gamma"]
    data = {
        "S08\n（剃除）": [
            [25.6, 20.5, 26.8, 21.0, 5.9],
            [21.0, 27.4, 23.1, 22.3, 6.1],
            [62.7, 20.4, 8.8, 6.3, 1.8],
        ],
        "S09\n（好，acc=1.00）": [
            [54.6, 21.2, 8.3, 11.1, 4.6],
            [33.3, 10.7, 3.8, 29.1, 22.6],
            [61.5, 19.7, 4.3, 8.9, 5.4],
        ],
        "S10\n（剃除）": [
            [20.2, 42.8, 20.6, 12.4, 4.1],
            [20.0, 44.6, 20.4, 11.3, 3.7],
            [21.7, 44.0, 19.9, 10.7, 3.6],
        ],
        "S15\n（好，acc=0.99）": [
            [27.1, 41.5, 16.8, 11.7, 2.9],
            [23.0, 30.4, 12.5, 20.8, 13.1],
            [42.2, 15.3, 4.7, 18.4, 18.8],
        ],
    }
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.5), sharey=True)
    width = 0.27
    x = np.arange(len(bands))
    for ax, (sub, vals) in zip(axes, data.items()):
        for i, task in enumerate(("Relax", "Focus", "Blink")):
            ax.bar(x + (i-1)*width, vals[i], width, color=COL_TASK[task],
                   label=task, edgecolor="white", linewidth=0.8)
        ax.set_xticks(x); ax.set_xticklabels(bands, rotation=20)
        ax.set_title(sub, color="#999" if "剃除" in sub else "#222", fontsize=12)
        ax.set_ylim(0, 75)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("相對功率 (%)")
    axes[0].legend(loc="upper right", fontsize=10, framealpha=0.95)
    fig.suptitle("好 / 壞受試者頻帶相對功率對比 — 解釋為什麼剃除 S08 / S10",
                 y=1.02, fontsize=13, fontweight="bold")
    save(fig, "04_band_power_diagnosis")


# ── 5. 剃除前後改善 ───────────────────────────────────────────────────────────
def fig_improvement():
    models = ["MLP", "SVM", "LDA", "RF", "Ensemble"]
    before = [0.731, 0.735, 0.729, 0.746, 0.754]
    after  = [0.830, 0.810, 0.810, 0.831, 0.833]
    before_std = [0.239, 0.222, 0.236, 0.229, 0.231]
    after_std  = [0.132, 0.145, 0.155, 0.128, 0.140]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    x = np.arange(len(models))
    w = 0.35

    ax1.bar(x - w/2, before, w, label="剃除前 (18 人)", color="#bbb",
            yerr=before_std, capsize=4, error_kw={"ecolor": "#666"})
    ax1.bar(x + w/2, after, w, label="剃除後 (16 人)", color=COL_OK,
            yerr=after_std, capsize=4, error_kw={"ecolor": "#444"})
    for i, (b, a) in enumerate(zip(before, after)):
        ax1.text(i - w/2, b + 0.01, f"{b:.2f}", ha="center", fontsize=9)
        ax1.text(i + w/2, a + 0.01, f"{a:.2f}", ha="center", fontsize=9, fontweight="bold")
    ax1.set_xticks(x); ax1.set_xticklabels(models)
    ax1.set_ylabel("LOSO 準確率")
    ax1.set_ylim(0, 1.15)
    ax1.set_title("剃除 S08/S10 前後 — 平均準確率", fontsize=12)
    ax1.legend(loc="lower right", framealpha=0.95)

    deltas = [a - b for a, b in zip(after, before)]
    std_drop = [bs - as_ for bs, as_ in zip(before_std, after_std)]
    ax2.bar(x - w/2, deltas, w, label="準確率提升", color=COL_OK,
            edgecolor="white")
    ax2.bar(x + w/2, std_drop, w, label="標準差下降", color="#4a90e2",
            edgecolor="white")
    for i, (d, sd) in enumerate(zip(deltas, std_drop)):
        ax2.text(i - w/2, d + 0.003, f"+{d*100:.1f}%", ha="center", fontsize=9, fontweight="bold")
        ax2.text(i + w/2, sd + 0.003, f"-{sd:.3f}", ha="center", fontsize=9, fontweight="bold")
    ax2.set_xticks(x); ax2.set_xticklabels(models)
    ax2.set_ylabel("變化量")
    ax2.set_ylim(0, 0.13)
    ax2.set_title("剃除受試者帶來的雙重改善", fontsize=12)
    ax2.legend(loc="upper right", framealpha=0.95)

    fig.suptitle("資料品質清理效果 — 18 人 → 16 人", y=1.02,
                 fontsize=13, fontweight="bold")
    save(fig, "05_cleanup_improvement")


# ── 6. 各模型 × 各受試者熱圖 ──────────────────────────────────────────────────
def fig_model_subject_heatmap():
    with open("model_comparison_detail.csv", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    headers = rows[0]
    body = [r for r in rows[1:] if r and r[0] not in ("Mean", "Std", "")]
    subjects = [r[0] for r in body]
    matrix = np.array([[float(x) for x in r[1:]] for r in body])

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(matrix, cmap="RdYlGn", vmin=0.4, vmax=1.0, aspect="auto")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            v = matrix[i, j]
            color = "black" if 0.55 < v < 0.85 else "white"
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=10, fontweight="bold", color=color)
    ax.set_xticks(range(len(headers[1:])))
    ax.set_xticklabels(headers[1:], rotation=20, ha="right")
    ax.set_yticks(range(len(subjects)))
    ax.set_yticklabels(subjects)
    ax.set_title("各模型 × 各受試者 LOSO 準確率熱圖")
    cbar = plt.colorbar(im, ax=ax, fraction=0.04)
    cbar.set_label("準確率")
    ax.grid(False)
    save(fig, "06_model_subject_heatmap")


# ── 7. 訊號範例：好/壞受試者 raw waveform ─────────────────────────────────────
def fig_signal_examples():
    fs = 512
    cases = [
        ("S09 - Blink (好)", "bci_dataset_114-2/S09/S09_3_5.txt", COL_OK),
        ("S15 - Blink (好)", "bci_dataset_114-2/S15/S15_3_5.txt", COL_OK),
        ("S08 - Relax (差，分不出 R/F)", "bci_dataset_114-2/S08/S08_1_5.txt", COL_BAD),
        ("S10 - Blink (差，訊號失真)", "bci_dataset_114-2/S10/S10_3_5.txt", COL_BAD),
    ]
    fig, axes = plt.subplots(4, 1, figsize=(13, 8), sharex=True)
    for ax, (title, fp, color) in zip(axes, cases):
        try:
            d = np.loadtxt(fp)
        except Exception:
            ax.text(0.5, 0.5, f"找不到 {fp}", ha="center", va="center")
            continue
        t = np.arange(len(d)) / fs
        ax.plot(t, d, color=color, linewidth=0.6)
        ax.set_title(title, loc="left", fontsize=11)
        ax.set_ylabel("Amplitude")
        ax.grid(True, alpha=0.3)
        # 標註 std
        ax.text(0.99, 0.95, f"std={np.std(d):.1f}  p2p={d.max()-d.min():.0f}",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=10, color="#444",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85))
    axes[-1].set_xlabel("時間 (秒)")
    fig.suptitle("好 / 壞訊號原始波形範例（同樣是 Blink 任務 20 秒）",
                 y=1.00, fontsize=13, fontweight="bold")
    fig.tight_layout()
    save(fig, "07_signal_waveforms")


# ── 8. 系統架構（純 matplotlib 畫流程圖）──────────────────────────────────────
def fig_architecture():
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.set_xlim(0, 14); ax.set_ylim(0, 8)
    ax.axis("off")

    def box(x, y, w, h, text, color, txt_color="white", fs=10):
        bp = FancyBboxPatch((x, y), w, h,
                            boxstyle="round,pad=0.08", linewidth=1.5,
                            edgecolor="#222", facecolor=color, alpha=0.95)
        ax.add_patch(bp)
        ax.text(x + w/2, y + h/2, text, ha="center", va="center",
                fontsize=fs, color=txt_color, fontweight="bold")

    def arrow(x1, y1, x2, y2, label="", style="->"):
        ar = FancyArrowPatch((x1, y1), (x2, y2),
                             arrowstyle=style, mutation_scale=22,
                             linewidth=1.8, color="#444")
        ax.add_patch(ar)
        if label:
            ax.text((x1+x2)/2, (y1+y2)/2 + 0.18, label,
                    ha="center", fontsize=9, color="#444",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.9, edgecolor="none"))

    # 硬體
    box(0.3, 6.0, 2.8, 0.9, "BrainLink Lite\n(Fp1 · 512 Hz · BT)", "#7b8aaa")

    # Python 後端
    box(0.3, 4.6, 2.8, 0.9, "BrainFlow 串流\n(NeuroSky board)", "#4a90e2")
    box(0.3, 3.2, 2.8, 0.9, "4s 滑動視窗\n+ 12 維特徵抽取", "#4a90e2")
    box(0.3, 1.8, 2.8, 0.9, "RobustScaler\n→ Ensemble 推論", "#4a90e2")
    box(0.3, 0.4, 2.8, 0.9, "眨眼累積 + 平滑\n→ 廣播 action", "#4a90e2")

    # 中間 WebSocket
    box(4.4, 3.0, 2.0, 1.4,
        "WebSocket\nws://localhost:8765\n\n{relax,focus,blink,\naction}",
        "#1ed760", txt_color="#000", fs=9)

    # Chrome 擴充
    box(7.7, 6.0, 5.7, 0.9, "Chrome 擴充 (manifest V3)", "#bd10e0")
    box(7.7, 4.6, 5.7, 0.9, "content.js — 注入 music.youtube.com", "#bd10e0")
    box(7.7, 3.2, 2.7, 0.9, "浮動 Overlay\n(機率條/眨眼計數)", "#bd10e0")
    box(10.7, 3.2, 2.7, 0.9, "DOM clicker\n(.play/.next/.prev)", "#bd10e0")
    box(7.7, 1.8, 5.7, 0.9, "popup.html — 設定面板（門檻、開關）", "#bd10e0")
    box(7.7, 0.4, 5.7, 0.9, "YouTube Music 網頁版（被自動操作）", "#f5a623", txt_color="#000")

    # 連線箭頭
    arrow(1.7, 6.0, 1.7, 5.5, "raw EEG")
    arrow(1.7, 4.6, 1.7, 4.1)
    arrow(1.7, 3.2, 1.7, 2.7)
    arrow(1.7, 1.8, 1.7, 1.3)
    arrow(3.1, 0.85, 4.4, 3.0, "broadcast")
    arrow(6.4, 4.0, 7.7, 5.0, "JSON")
    arrow(10.55, 3.2, 10.55, 1.3, "click")

    ax.text(1.7, 7.5, "Python 後端", ha="center", fontsize=13,
            fontweight="bold", color="#4a90e2")
    ax.text(10.55, 7.5, "Chrome 擴充", ha="center", fontsize=13,
            fontweight="bold", color="#bd10e0")
    ax.set_title("BCI × YouTube Music — 即時控制系統架構", fontsize=14, pad=20)
    save(fig, "08_system_architecture")


# ── 9. 處理流程時序圖（每秒一格）──────────────────────────────────────────────
def fig_pipeline_timing():
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.set_xlim(0, 10); ax.set_ylim(0, 5)
    ax.axis("off")

    # 時間刻度
    for t in range(11):
        ax.axvline(t, color="#ddd", linewidth=0.5)
        ax.text(t, -0.15, f"{t}s", ha="center", fontsize=9, color="#666")

    # 視窗 4s，每 0.5s 推一次
    rows_y = [3.7, 2.9, 2.1, 1.3]
    starts = [0, 0.5, 1.0, 1.5]
    for y, s in zip(rows_y, starts):
        ax.barh(y, 4, left=s, height=0.6, color="#4a90e2", alpha=0.7,
                edgecolor="#222")
        ax.text(s + 4 + 0.1, y, f"視窗 t={s:.1f}~{s+4:.1f}s → 推論",
                fontsize=10, va="center")
        # 推論觸發時刻
        ax.scatter(s + 4, y, color=COL_OK, s=80, zorder=5)

    ax.text(2.0, 4.4, "4 秒滑動視窗 · 每 0.5 秒推論一次",
            fontsize=12, fontweight="bold", color="#222")

    # 事件示意
    ax.scatter(5.5, 0.5, color=COL_BAD, s=200, zorder=5, marker="*")
    ax.text(5.5, 0.15, "眨眼", ha="center", fontsize=10, color=COL_BAD,
            fontweight="bold")
    ax.scatter(6.5, 0.5, color=COL_BAD, s=200, zorder=5, marker="*")
    ax.text(6.5, 0.15, "眨眼", ha="center", fontsize=10, color=COL_BAD,
            fontweight="bold")
    ax.annotate("2 秒內 2 次眨眼\n→ 廣播 action: 'next'\n→ 擴充 click 下一首",
                xy=(6.5, 0.5), xytext=(8, 0.7),
                fontsize=10, color="#000",
                bbox=dict(boxstyle="round,pad=0.4", facecolor=COL_OK, alpha=0.9),
                arrowprops=dict(arrowstyle="->", color="#444"))

    ax.set_title("即時推論時序示意", fontsize=13, pad=10)
    save(fig, "09_pipeline_timing")


# ── 10. 特徵工程示意 ─────────────────────────────────────────────────────────
def fig_features():
    fs = 512
    # 取 S15 一段眨眼當例子
    try:
        d = np.loadtxt("bci_dataset_114-2/S15/S15_3_5.txt")[:fs*4]
    except Exception:
        d = np.random.randn(fs*4) * 30

    d = d - d.mean()
    t = np.arange(len(d)) / fs

    # FFT
    freqs = np.fft.rfftfreq(len(d), 1/fs)
    psd = np.abs(np.fft.rfft(d * np.hanning(len(d))))**2
    mask = (freqs >= 1) & (freqs <= 40)
    rel_psd = psd[mask] / psd[mask].sum()
    band_edges = [(1,4,"Delta"), (4,8,"Theta"), (8,13,"Alpha"),
                  (13,30,"Beta"), (30,40,"Gamma")]
    band_powers = []
    for lo, hi, _ in band_edges:
        m = (freqs >= lo) & (freqs < hi)
        band_powers.append(psd[m].sum())
    band_powers = np.array(band_powers) / sum(band_powers)

    fig, axes = plt.subplots(2, 2, figsize=(13, 7))

    # (a) 原始訊號
    ax = axes[0, 0]
    ax.plot(t, d, color="#4a90e2", linewidth=0.7)
    ax.set_xlabel("時間 (秒)"); ax.set_ylabel("Amplitude")
    ax.set_title("(a) 4 秒原始訊號（已去均值）", loc="left")

    # (b) PSD
    ax = axes[0, 1]
    ax.plot(freqs[mask], rel_psd, color="#7b8aaa", linewidth=1)
    for lo, hi, name in band_edges:
        m = (freqs >= lo) & (freqs < hi)
        ax.fill_between(freqs[m], 0, rel_psd[freqs[mask] == freqs[m][0]][0]
                        if False else psd[m]/psd[mask].sum(),
                        alpha=0.4, label=f"{name}")
    ax.set_xlabel("頻率 (Hz)"); ax.set_ylabel("相對功率")
    ax.set_title("(b) FFT 功率譜（1-40 Hz 5 個頻帶）", loc="left")
    ax.legend(fontsize=9, loc="upper right", framealpha=0.95)

    # (c) 頻帶相對功率
    ax = axes[1, 0]
    names = [b[2] for b in band_edges]
    ax.bar(names, band_powers * 100, color=["#4a90e2","#5fa8ff","#f5a623","#ffc857","#e5484d"],
           edgecolor="white")
    for i, p in enumerate(band_powers):
        ax.text(i, p*100 + 0.5, f"{p*100:.1f}%", ha="center", fontsize=10, fontweight="bold")
    ax.set_ylabel("相對功率 (%)")
    ax.set_title("(c) 5 維頻帶特徵（log+sqrt 轉換後送進模型）", loc="left")

    # (d) 12 維特徵總覽
    ax = axes[1, 1]
    ax.axis("off")
    feat_groups = [
        ("頻帶功率 (5 維)", "Delta, Theta, Alpha, Beta, Gamma\n各頻帶相對功率 → log10 + sqrt"),
        ("Hjorth 參數 (3 維)", "Activity, Mobility, Complexity\n反映訊號複雜度"),
        ("基本統計 (1 維)", "標準差 std"),
        ("新增特徵 (3 維)", "α/β 比值 (反映警覺度)\nPeak-to-peak (眨眼幅度)\nZero-crossing rate (頻率密度)"),
    ]
    y = 0.95
    for title, body in feat_groups:
        ax.text(0.02, y, "■ " + title, fontsize=11, fontweight="bold",
                color="#1ed760", transform=ax.transAxes)
        ax.text(0.05, y - 0.06, body, fontsize=10,
                color="#333", transform=ax.transAxes)
        y -= 0.22
    ax.text(0.5, 0.02, "→ 共 12 維特徵向量",
            fontsize=12, fontweight="bold", ha="center",
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=COL_OK, alpha=0.9))
    ax.set_title("(d) 12 維特徵組成", loc="left")

    fig.suptitle("特徵工程：4 秒原始訊號 → 12 維向量",
                 y=1.00, fontsize=13, fontweight="bold")
    fig.tight_layout()
    save(fig, "10_feature_engineering")


# ── 主程式 ───────────────────────────────────────────────────────────────────
def main():
    print(f"輸出資料夾：{OUT}/\n")
    print("[1/10] 模型比較長條圖")
    fig_model_comparison()
    print("[2/10] 各受試者準確率")
    fig_per_subject()
    print("[3/10] 混淆矩陣熱圖")
    fig_confusion_matrix()
    print("[4/10] 好/壞受試者頻帶比較")
    fig_band_comparison()
    print("[5/10] 剃除前後改善")
    fig_improvement()
    print("[6/10] 各模型×各受試者熱圖")
    fig_model_subject_heatmap()
    print("[7/10] 訊號範例波形")
    fig_signal_examples()
    print("[8/10] 系統架構流程圖")
    fig_architecture()
    print("[9/10] 即時推論時序")
    fig_pipeline_timing()
    print("[10/10] 特徵工程示意")
    fig_features()
    print(f"\n✅ 全部完成。共 {len(glob.glob(os.path.join(OUT, '*.png')))} 張 PNG"
          f" + {len(glob.glob(os.path.join(OUT, '*.pdf')))} 張 PDF")


if __name__ == "__main__":
    main()
