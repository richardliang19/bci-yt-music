"""
最終模型訓練 + 存檔
─────────────────────────────────
讀取 16 位受試者（更新版資料集，已剃除 S13/S14），用 MLP（更新版資料集 LOSO
最佳，0.879）訓練在全部資料上，存成 bci_model.pkl 給 bci_server.py 即時使用。
"""

import os
import glob
import joblib
import numpy as np
from sklearn.preprocessing import RobustScaler
from sklearn.neural_network import MLPClassifier
from sklearn.utils.class_weight import compute_sample_weight

# 直接複用 train_compare.py 裡的 Config 與特徵函式
from train_compare import (
    Config, extract_features, create_segments,
)


MODEL_OUT = "bci_model.pkl"


def load_all():
    seg_len = int(Config.SEGMENT_LENGTH * Config.SAMPLING_RATE)
    overlap = int(seg_len * Config.OVERLAP_RATIO)

    folders = sorted([f.path for f in os.scandir(Config.DATASET_PATH) if f.is_dir()])
    folders = [f for f in folders if os.path.basename(f) not in Config.EXCLUDED_SUBJECTS]
    print(f"使用 {len(folders)} 位受試者（已排除 {sorted(Config.EXCLUDED_SUBJECTS)}）")

    segs_all, labels_all = [], []
    for folder in folders:
        for label, pattern in enumerate(["*_1_*.txt", "*_2_*.txt", "*_3_*.txt"]):
            for fp in glob.glob(os.path.join(folder, pattern)):
                try:
                    data = np.loadtxt(fp)
                    s = create_segments(data, seg_len, overlap)
                    segs_all.extend(s)
                    labels_all.extend([label] * len(s))
                except Exception as e:
                    print(f"  讀取失敗 {fp}: {e}")

    print(f"總片段數：{len(segs_all)}")
    X = extract_features(segs_all)
    y = np.array(labels_all)
    return X, y


def build_model():
    # MLP：更新版資料集 LOSO 最佳（0.879）；設定沿用 train_compare.py
    return MLPClassifier(
        hidden_layer_sizes=(128, 64), max_iter=200,
        learning_rate_init=0.005, alpha=0.005,
        activation="relu", solver="adam", batch_size=128,
        early_stopping=True, n_iter_no_change=12, validation_fraction=0.1,
        random_state=Config.RANDOM_STATE, verbose=False,
    )


def main():
    X, y = load_all()
    print(f"特徵維度：{X.shape[1]}")

    scaler = RobustScaler(quantile_range=(25, 75))
    Xs = scaler.fit_transform(X)

    sw = compute_sample_weight("balanced", y).astype(np.float64)
    print("\n訓練 MLP 中…")
    model = build_model()
    try:
        model.fit(Xs, y, sample_weight=sw)
    except TypeError:
        model.fit(Xs, y)

    train_acc = model.score(Xs, y)
    print(f"訓練集準確率（不是泛化指標，僅 sanity check）：{train_acc:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "labels": {0: "Relax", 1: "Focus", 2: "Blink"},
        "config": {
            "sampling_rate": Config.SAMPLING_RATE,
            "segment_length": Config.SEGMENT_LENGTH,
            "bp_low": Config.BP_LOW,
            "bp_high": Config.BP_HIGH,
            "bands": list(Config.BANDS),
            "feature_dim": X.shape[1],
        },
    }
    joblib.dump(bundle, MODEL_OUT)
    print(f"\n✅ 模型已存至：{MODEL_OUT}（{os.path.getsize(MODEL_OUT)/1024/1024:.1f} MB）")


if __name__ == "__main__":
    main()
