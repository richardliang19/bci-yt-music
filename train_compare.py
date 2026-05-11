"""
模型比較訓練腳本
比較：MLP（原版）、SVM、LDA、Random Forest、Soft Voting Ensemble
特徵：沿用 main.py 的 9 維特徵 + 新增 Alpha/Beta Ratio、Peak-to-Peak、Zero-Crossing Rate
驗證：LOSO (Leave-One-Subject-Out)
"""

import numpy as np
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.neural_network import MLPClassifier
from sklearn.svm import SVC
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.utils.class_weight import compute_sample_weight
import os
import glob
import warnings
import time
import csv

warnings.filterwarnings("ignore")

class Config:
    DATASET_PATH = "bci_dataset_114-2"
    EXCLUDED_SUBJECTS = frozenset({"S08", "S10"})  # 訊號品質不佳，剃除
    SAMPLING_RATE = 512
    SEGMENT_LENGTH = 4
    OVERLAP_RATIO = 0.5
    RANDOM_STATE = 42
    BP_LOW = 1.0
    BP_HIGH = 40.0
    BANDS = ((1, 4), (4, 8), (8, 13), (13, 30), (30, 40))
    MAJORITY_VOTE_WIN = 15
    QC_BAD_FILE_NAMES = frozenset({
        "b12901077_3_6.txt", "b12901077_3_7.txt",
        "b12901151_1_11.txt", "b12901151_1_12.txt", "b12901151_1_20.txt",
    })
    QC_BAD_TRAIN_WEIGHT_MULT = 0.25


# ── 訊號處理工具 ──────────────────────────────────────────────────────────────

def bandpass_fft(sig, fs, low, high):
    x = np.asarray(sig, dtype=np.float64) - np.mean(sig)
    n = x.shape[0]
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    Xf = np.fft.rfft(x) * ((freqs >= low) & (freqs <= high)).astype(np.float64)
    return np.fft.irfft(Xf, n=n)


def hjorth(x):
    x = np.asarray(x, dtype=np.float64)
    var = np.var(x)
    if var < 1e-12:
        return 0.0, 0.0, 0.0
    dx = np.diff(x)
    var_d = np.var(dx)
    mobility = np.sqrt(var_d / var) if var_d >= 1e-12 else 0.0
    ddx = np.diff(dx)
    var_dd = np.var(ddx)
    complexity = (np.sqrt(var_dd / var_d) / mobility) if (var_d >= 1e-12 and mobility > 1e-12) else 0.0
    return float(var), float(mobility), float(complexity)


def band_powers_fft(x, fs, bands):
    n = x.shape[0]
    xw = x * np.hanning(n)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    psd = np.abs(np.fft.rfft(xw)) ** 2
    return np.array([float(np.sum(psd[(freqs >= lo) & (freqs < hi)])) for lo, hi in bands])


# ── 特徵提取（9 維原版 + 3 維新增 = 12 維）────────────────────────────────────

def extract_features(segments):
    fs = float(Config.SAMPLING_RATE)
    eps = 1e-12
    features = []
    for seg in segments:
        xf = bandpass_fft(seg, fs, Config.BP_LOW, Config.BP_HIGH)
        bp = band_powers_fft(xf, fs, Config.BANDS)
        total = bp.sum() + eps
        rel = bp / total
        rel_root = np.sqrt(np.clip(rel, 0.0, 1.0))
        log_rel = np.log10(rel_root + eps)          # 5 維頻帶功率

        act, mob, comp = hjorth(xf)                  # 3 維 Hjorth
        std_val = np.std(xf)                         # 1 維標準差

        # ── 新增特徵 ──
        alpha_idx, beta_idx = 2, 3                   # BANDS 裡 alpha=index2, beta=index3
        alpha_beta_ratio = float(bp[alpha_idx] / (bp[beta_idx] + eps))  # Alpha/Beta 比值
        peak_to_peak = float(np.max(seg) - np.min(seg))                 # 峰峰值
        zero_crossing = float(np.sum(np.diff(np.sign(xf)) != 0)) / len(xf)  # 過零率

        feat = np.concatenate([
            log_rel,
            [np.log10(act + eps), mob, comp, std_val],
            [np.log10(alpha_beta_ratio + eps), peak_to_peak, zero_crossing],
        ])
        features.append(feat)
    return np.array(features)


# ── 資料載入 ──────────────────────────────────────────────────────────────────

def create_segments(data, seg_len, overlap):
    if len(data) < seg_len:
        return []
    segments, start = [], 0
    step = seg_len - overlap
    while start + seg_len <= len(data):
        seg = data[start: start + seg_len].astype(np.float64)
        segments.append(seg - np.mean(seg))
        start += step
    return segments


def load_all_subjects():
    if not os.path.exists(Config.DATASET_PATH):
        print(f"找不到資料夾：{Config.DATASET_PATH}")
        return None, None, None, None, None

    all_folders = sorted([f.path for f in os.scandir(Config.DATASET_PATH) if f.is_dir()])
    subject_folders = [f for f in all_folders if os.path.basename(f) not in Config.EXCLUDED_SUBJECTS]
    excluded = [os.path.basename(f) for f in all_folders if os.path.basename(f) in Config.EXCLUDED_SUBJECTS]

    if len(subject_folders) < 2:
        print("受試者資料夾不足")
        return None, None, None, None, None

    if excluded:
        print(f"⚠ 已排除受試者：{', '.join(excluded)}（訊號品質不佳）")
    print(f"找到 {len(subject_folders)} 位受試者，載入資料中...\n")

    seg_len = int(Config.SEGMENT_LENGTH * Config.SAMPLING_RATE)
    overlap = int(seg_len * Config.OVERLAP_RATIO)

    all_features, all_labels, all_subjects, all_blocks = [], [], [], []
    qc_bad_block_ids = set()
    file_bid = 0

    for folder in subject_folders:
        sid = os.path.basename(folder)
        task_feats, task_labels, task_bids = [], [], []

        for label, pattern in enumerate(["*_1_*.txt", "*_2_*.txt", "*_3_*.txt"]):
            for file in glob.glob(os.path.join(folder, pattern)):
                fn = os.path.basename(file)
                try:
                    data = np.loadtxt(file)
                    segs = create_segments(data, seg_len, overlap)
                    task_feats.extend(segs)
                    task_labels.extend([label] * len(segs))
                    task_bids.extend([file_bid] * len(segs))
                    if fn in Config.QC_BAD_FILE_NAMES:
                        qc_bad_block_ids.add(file_bid)
                except Exception as e:
                    print(f"  讀取失敗 {file}: {e}")
                file_bid += 1

        if not task_feats:
            print(f"  {sid}：資料不足，跳過")
            continue

        feats = extract_features(task_feats)
        all_features.append(feats)
        all_labels.append(np.array(task_labels))
        all_subjects.extend([sid] * len(task_labels))
        all_blocks.append(np.array(task_bids, dtype=int))

        counts = [task_labels.count(i) for i in range(3)]
        print(f"  {sid}: Relax={counts[0]} Focus={counts[1]} Blink={counts[2]} 片段")

    if not all_features:
        return None, None, None, None, None

    return (
        np.vstack(all_features),
        np.hstack(all_labels),
        all_subjects,
        np.concatenate(all_blocks),
        frozenset(qc_bad_block_ids),
    )


# ── 後處理：同窗機率平均 ──────────────────────────────────────────────────────

def majority_proba_smooth(proba_te, test_row_idx, blocks_full, win):
    out = np.empty(len(proba_te), dtype=int)
    blk_te = blocks_full[test_row_idx]
    half = win // 2
    for b in np.unique(blk_te):
        loc = np.where(blk_te == b)[0]
        loc = loc[np.argsort(test_row_idx[loc])]
        pblk = proba_te[loc]
        new_local = np.empty(len(loc), dtype=int)
        for k in range(len(loc)):
            lo, hi = max(0, k - half), min(len(loc), k + half + 1)
            new_local[k] = int(np.argmax(pblk[lo:hi].mean(axis=0)))
        out[loc] = new_local
    return out


# ── 模型定義 ──────────────────────────────────────────────────────────────────

def build_models():
    # MLP：縮小網路、用 early_stopping 加速（針對 18 人大資料集）
    mlp = MLPClassifier(
        hidden_layer_sizes=(128, 64), max_iter=80,
        learning_rate_init=0.005, alpha=0.005,
        activation="relu", solver="adam", batch_size=128,
        early_stopping=True, n_iter_no_change=8, validation_fraction=0.1,
        random_state=Config.RANDOM_STATE, verbose=False,
    )
    # SVM：RBF 在 14k 樣本太慢 → 改用 LinearSVC + Calibrated 取得 proba（仍是 SVM 思想）
    from sklearn.svm import LinearSVC
    from sklearn.calibration import CalibratedClassifierCV
    svm = CalibratedClassifierCV(
        LinearSVC(C=1.0, max_iter=2000, random_state=Config.RANDOM_STATE),
        cv=3, method="sigmoid"
    )
    lda = LinearDiscriminantAnalysis(solver="svd")
    rf = RandomForestClassifier(
        n_estimators=150, max_depth=None,
        random_state=Config.RANDOM_STATE, n_jobs=-1
    )
    ensemble = VotingClassifier(
        estimators=[
            ("rf", RandomForestClassifier(n_estimators=150, random_state=Config.RANDOM_STATE, n_jobs=-1)),
            ("svm", CalibratedClassifierCV(LinearSVC(C=1.0, max_iter=2000, random_state=Config.RANDOM_STATE), cv=3)),
            ("lda", LinearDiscriminantAnalysis(solver="svd")),
        ],
        voting="soft", n_jobs=-1,
    )
    return {"MLP": mlp, "SVM(Linear)": svm, "LDA": lda, "RandomForest": rf, "Ensemble(RF+SVM+LDA)": ensemble}


# ── LOSO 主流程 ───────────────────────────────────────────────────────────────

def run_loso(X, y, subjects, blocks, qc_bad_blocks, model_name, model_proto):
    unique_subjects = sorted(set(subjects))
    subjects_arr = np.array(subjects)
    blocks = np.asarray(blocks, dtype=int)

    accuracies, cms = [], []

    for i, test_sub in enumerate(unique_subjects, 1):
        t_fold = time.time()
        tr_mask = subjects_arr != test_sub
        te_mask = ~tr_mask

        X_tr, X_te = X[tr_mask], X[te_mask]
        y_tr, y_te = y[tr_mask], y[te_mask]

        scaler = RobustScaler(quantile_range=(25, 75))
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        tr_idx = np.flatnonzero(tr_mask)
        sw = compute_sample_weight("balanced", y_tr).astype(np.float64)
        for k, bid in enumerate(blocks[tr_idx]):
            if bid in qc_bad_blocks:
                sw[k] *= Config.QC_BAD_TRAIN_WEIGHT_MULT

        import copy
        model = copy.deepcopy(model_proto)

        try:
            model.fit(X_tr_s, y_tr, sample_weight=sw)
        except TypeError:
            model.fit(X_tr_s, y_tr)

        proba = model.predict_proba(X_te_s)
        te_idx = np.flatnonzero(te_mask)
        y_pred = majority_proba_smooth(proba, te_idx, blocks, Config.MAJORITY_VOTE_WIN)

        acc = accuracy_score(y_te, y_pred)
        cm = confusion_matrix(y_te, y_pred, labels=[0, 1, 2])
        accuracies.append(acc)
        cms.append(cm)

        elapsed = time.time() - t_fold
        running_mean = np.mean(accuracies)
        print(f"  [{i:>2}/18] {test_sub}: acc={acc:.3f}  (耗時 {elapsed:.1f}s, 累積平均={running_mean:.3f})", flush=True)

    return np.array(accuracies), cms


# ── 儲存 CSV 結果 ─────────────────────────────────────────────────────────────

def save_results_csv(all_results, unique_subjects):
    # 各受試者詳細結果
    with open("model_comparison_detail.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        model_names = list(all_results.keys())
        writer.writerow(["Subject"] + model_names)
        for i, sub in enumerate(unique_subjects):
            row = [sub] + [f"{all_results[m]['accs'][i]:.4f}" for m in model_names]
            writer.writerow(row)
        writer.writerow([])
        writer.writerow(["Mean"] + [f"{all_results[m]['mean']:.4f}" for m in model_names])
        writer.writerow(["Std"]  + [f"{all_results[m]['std']:.4f}"  for m in model_names])
    print("詳細結果已存為 model_comparison_detail.csv")

    # 最佳模型混淆矩陣
    best_name = max(all_results, key=lambda m: all_results[m]["mean"])
    total_cm = np.sum(all_results[best_name]["cms"], axis=0)
    with open("best_model_confusion_matrix.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([f"Confusion Matrix ({best_name})", "Pred:Relax", "Pred:Focus", "Pred:Blink"])
        for label, row in zip(["Actual:Relax", "Actual:Focus", "Actual:Blink"], total_cm):
            writer.writerow([label] + list(row))
    print(f"最佳模型（{best_name}）混淆矩陣已存為 best_model_confusion_matrix.csv")


# ── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("BCI 模型比較訓練腳本（12 維特徵，18 受試者，LOSO）")
    print("=" * 60)

    X, y, subjects, blocks, qc_bad_blocks = load_all_subjects()
    if X is None:
        return

    print(f"\n特徵維度：{X.shape[1]}  /  總片段數：{X.shape[0]}\n")

    unique_subjects = sorted(set(subjects))
    models = build_models()
    all_results = {}

    for name, proto in models.items():
        print(f"\n{'='*50}")
        print(f"▶ 訓練模型：{name}")
        print(f"{'='*50}")
        t0 = time.time()
        accs, cms = run_loso(X, y, subjects, blocks, qc_bad_blocks, name, proto)
        elapsed = time.time() - t0

        for sub, acc in zip(unique_subjects, accs):
            print(f"  {sub}: {acc:.3f}")

        mean_acc = np.mean(accs)
        std_acc = np.std(accs)
        print(f"\n  → 平均準確率：{mean_acc:.3f} ± {std_acc:.3f}  (耗時 {elapsed:.1f}s)")
        all_results[name] = {"accs": accs, "cms": cms, "mean": mean_acc, "std": std_acc}

    # 最終摘要
    print("\n" + "=" * 60)
    print("📊 最終比較摘要")
    print("=" * 60)
    print(f"{'模型':<25} {'平均準確率':>10} {'標準差':>8}")
    print("-" * 45)
    for name in models:
        r = all_results[name]
        flag = ""
        if r["mean"] >= 0.70:
            flag = " ✓ (>70%)"
        elif r["mean"] >= 0.65:
            flag = " ✓ (>65%)"
        print(f"{name:<25} {r['mean']:>10.3f} {r['std']:>8.3f}{flag}")

    save_results_csv(all_results, unique_subjects)


if __name__ == "__main__":
    main()
