"""
產生期末專題進度報告 HTML（單檔，內嵌 SVG，可直接截圖或列印）
讀取：
  - model_comparison_detail.csv
  - best_model_confusion_matrix.csv
  - result_v2.txt（取摘要）
輸出：
  - progress_report.html
"""

import csv
import datetime
import html


# ── 讀資料 ─────────────────────────────────────────────────────────────────
def read_model_csv(path="model_comparison_detail.csv"):
    rows, mean, std = [], None, None
    with open(path, encoding="utf-8") as f:
        reader = list(csv.reader(f))
    headers = reader[0]
    for r in reader[1:]:
        if not r or not r[0]:
            continue
        if r[0] == "Mean":
            mean = [float(x) for x in r[1:]]
        elif r[0] == "Std":
            std = [float(x) for x in r[1:]]
        else:
            rows.append([r[0]] + [float(x) for x in r[1:]])
    return headers, rows, mean, std


def read_cm_csv(path="best_model_confusion_matrix.csv"):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    title = rows[0][0]
    cm = [[int(x) for x in r[1:]] for r in rows[1:]]
    return title, cm


# ── SVG 工具 ──────────────────────────────────────────────────────────────
def svg_bar_chart(labels, values, stds=None, width=700, height=320,
                  ymax=1.0, title="", colors=None, baseline=None):
    """畫一張橫條圖（每個模型一條）"""
    margin = {"top": 40, "right": 30, "bottom": 60, "left": 80}
    iw = width - margin["left"] - margin["right"]
    ih = height - margin["top"] - margin["bottom"]
    n = len(labels)
    bar_w = iw / n * 0.6
    gap = iw / n * 0.4

    if colors is None:
        colors = ["#4a90e2", "#f5a623", "#7ed321", "#bd10e0", "#1ed760"]

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'style="background:#fafafa;border-radius:8px;font-family:sans-serif">']
    if title:
        parts.append(f'<text x="{width/2}" y="22" text-anchor="middle" '
                     f'font-size="14" font-weight="600" fill="#333">{html.escape(title)}</text>')

    # Y 軸格線
    for i in range(0, 11, 2):
        y = margin["top"] + ih * (1 - i/10)
        v = i/10 * ymax
        parts.append(f'<line x1="{margin["left"]}" x2="{width-margin["right"]}" '
                     f'y1="{y}" y2="{y}" stroke="#e0e0e0" stroke-width="1"/>')
        parts.append(f'<text x="{margin["left"]-8}" y="{y+4}" text-anchor="end" '
                     f'font-size="11" fill="#777">{v:.1f}</text>')

    # 基準線（如門檻 0.7）
    if baseline is not None:
        y = margin["top"] + ih * (1 - baseline/ymax)
        parts.append(f'<line x1="{margin["left"]}" x2="{width-margin["right"]}" '
                     f'y1="{y}" y2="{y}" stroke="#e5484d" stroke-width="1" stroke-dasharray="4,4"/>')
        parts.append(f'<text x="{width-margin["right"]-4}" y="{y-4}" text-anchor="end" '
                     f'font-size="10" fill="#e5484d">門檻 {baseline:.0%}</text>')

    # 條
    for i, (lbl, v) in enumerate(zip(labels, values)):
        x = margin["left"] + i * (bar_w + gap) + gap/2
        bh = ih * (v / ymax)
        y = margin["top"] + ih - bh
        c = colors[i % len(colors)]
        parts.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" '
                     f'fill="{c}" rx="3"/>')
        # 數值
        parts.append(f'<text x="{x + bar_w/2}" y="{y - 6}" text-anchor="middle" '
                     f'font-size="12" font-weight="600" fill="#222">{v:.3f}</text>')
        if stds:
            parts.append(f'<text x="{x + bar_w/2}" y="{y + bh + 16}" text-anchor="middle" '
                         f'font-size="10" fill="#666">±{stds[i]:.3f}</text>')
        # X 軸標籤
        parts.append(f'<text x="{x + bar_w/2}" y="{height - margin["bottom"] + 25}" '
                     f'text-anchor="middle" font-size="11" fill="#333">'
                     f'{html.escape(lbl)}</text>')

    parts.append('</svg>')
    return "\n".join(parts)


def svg_per_subject_chart(rows, headers, model_idx, width=900, height=340,
                          excluded=("S08", "S10")):
    """畫單一模型的各受試者 LOSO 準確率"""
    margin = {"top": 50, "right": 20, "bottom": 50, "left": 50}
    iw = width - margin["left"] - margin["right"]
    ih = height - margin["top"] - margin["bottom"]
    n = len(rows)
    bar_w = iw / n * 0.7
    step = iw / n

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'style="background:#fafafa;border-radius:8px;font-family:sans-serif">']
    parts.append(f'<text x="{width/2}" y="22" text-anchor="middle" '
                 f'font-size="14" font-weight="600" fill="#333">'
                 f'{html.escape(headers[model_idx])} — 各受試者 LOSO 準確率</text>')
    parts.append(f'<text x="{width/2}" y="40" text-anchor="middle" '
                 f'font-size="11" fill="#888">紅虛線 = 隨機 baseline (33%)，'
                 f'灰色 = 已排除受試者</text>')

    # baseline 33%
    y_base = margin["top"] + ih * (1 - 0.333)
    parts.append(f'<line x1="{margin["left"]}" x2="{width-margin["right"]}" '
                 f'y1="{y_base}" y2="{y_base}" stroke="#e5484d" stroke-width="1" stroke-dasharray="3,3"/>')
    # 70% 線
    y_70 = margin["top"] + ih * (1 - 0.7)
    parts.append(f'<line x1="{margin["left"]}" x2="{width-margin["right"]}" '
                 f'y1="{y_70}" y2="{y_70}" stroke="#1ed760" stroke-width="1" stroke-dasharray="3,3"/>')
    parts.append(f'<text x="{width-margin["right"]-4}" y="{y_70-4}" text-anchor="end" '
                 f'font-size="10" fill="#1ed760">70%</text>')

    for i, r in enumerate(rows):
        sub = r[0]
        v = r[model_idx]
        x = margin["left"] + i * step + (step - bar_w)/2
        bh = ih * v
        y = margin["top"] + ih - bh
        in_excl = sub in excluded
        if in_excl:
            color = "#999"
        elif v >= 0.85:
            color = "#1ed760"
        elif v >= 0.70:
            color = "#f5a623"
        else:
            color = "#e5484d"
        parts.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bh}" '
                     f'fill="{color}" rx="2"/>')
        parts.append(f'<text x="{x + bar_w/2}" y="{y - 4}" text-anchor="middle" '
                     f'font-size="9" fill="#333">{v:.2f}</text>')
        parts.append(f'<text x="{x + bar_w/2}" y="{height - margin["bottom"] + 14}" '
                     f'text-anchor="middle" font-size="10" fill="{"#999" if in_excl else "#333"}" '
                     f'font-weight="{("normal" if not in_excl else "italic")}">'
                     f'{sub}{" *" if in_excl else ""}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def svg_confusion_matrix(cm, labels=("Relax", "Focus", "Blink"), width=420, height=380):
    """3x3 混淆矩陣熱圖（含百分比與絕對數）"""
    margin = {"top": 70, "right": 30, "bottom": 50, "left": 80}
    cell = min(width - margin["left"] - margin["right"],
               height - margin["top"] - margin["bottom"]) / len(labels)
    cell = min(cell, 90)
    grid_w = cell * len(labels)

    # 用 row-normalized 上色
    row_sums = [sum(r) for r in cm]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'style="background:#fafafa;border-radius:8px;font-family:sans-serif">']
    parts.append(f'<text x="{width/2}" y="24" text-anchor="middle" '
                 f'font-size="14" font-weight="600" fill="#333">混淆矩陣（Ensemble）</text>')
    parts.append(f'<text x="{width/2}" y="44" text-anchor="middle" '
                 f'font-size="11" fill="#888">數字：樣本數 ／ 顏色：每列百分比</text>')

    # 欄標題（預測）
    for j, lbl in enumerate(labels):
        x = margin["left"] + j * cell + cell/2
        parts.append(f'<text x="{x}" y="{margin["top"] - 8}" text-anchor="middle" '
                     f'font-size="12" font-weight="600" fill="#333">{lbl}</text>')

    # 列標題（實際）
    for i, lbl in enumerate(labels):
        y = margin["top"] + i * cell + cell/2 + 4
        parts.append(f'<text x="{margin["left"] - 8}" y="{y}" text-anchor="end" '
                     f'font-size="12" font-weight="600" fill="#333">{lbl}</text>')

    # 軸標題
    parts.append(f'<text x="{margin["left"] + grid_w/2}" y="{margin["top"] - 28}" '
                 f'text-anchor="middle" font-size="11" fill="#666">預測類別</text>')
    parts.append(f'<text x="{20}" y="{margin["top"] + grid_w/2}" text-anchor="middle" '
                 f'font-size="11" fill="#666" transform="rotate(-90, 20, {margin["top"] + grid_w/2})">'
                 f'實際類別</text>')

    # 格子
    for i, row in enumerate(cm):
        for j, v in enumerate(row):
            pct = v / row_sums[i] if row_sums[i] else 0
            x = margin["left"] + j * cell
            y = margin["top"] + i * cell
            # 用綠色漸層（對角線越深 = 越好）
            if i == j:
                # 綠色 0~100% → #e8f5e9 to #1ed760
                r_, g_, b_ = (
                    int(232 - (232-30)*pct),
                    int(245 - (245-215)*pct),
                    int(233 - (233-96)*pct),
                )
            else:
                # 紅色淺底
                r_, g_, b_ = (
                    int(255 - (255-229)*pct),
                    int(255 - (255-72)*pct),
                    int(255 - (255-77)*pct),
                )
            color = f"rgb({r_},{g_},{b_})"
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
                         f'fill="{color}" stroke="#fff" stroke-width="2"/>')
            text_color = "#fff" if pct > 0.55 else "#222"
            parts.append(f'<text x="{x + cell/2}" y="{y + cell/2 - 2}" '
                         f'text-anchor="middle" font-size="14" font-weight="700" '
                         f'fill="{text_color}">{v}</text>')
            parts.append(f'<text x="{x + cell/2}" y="{y + cell/2 + 14}" '
                         f'text-anchor="middle" font-size="10" '
                         f'fill="{text_color}">{pct*100:.1f}%</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def svg_band_compare(width=920, height=320):
    """S08/S10/S09/S15 三個任務頻帶比較（從 diagnose 資料硬編，便於報告解釋）"""
    # 各受試者 [Relax, Focus, Blink] x [Delta, Theta, Alpha, Beta, Gamma]
    data = {
        "S08 (剃除)": [
            [25.6, 20.5, 26.8, 21.0, 5.9],
            [21.0, 27.4, 23.1, 22.3, 6.1],
            [62.7, 20.4, 8.8, 6.3, 1.8],
        ],
        "S09 (好)": [
            [54.6, 21.2, 8.3, 11.1, 4.6],
            [33.3, 10.7, 3.8, 29.1, 22.6],
            [61.5, 19.7, 4.3, 8.9, 5.4],
        ],
        "S10 (剃除)": [
            [20.2, 42.8, 20.6, 12.4, 4.1],
            [20.0, 44.6, 20.4, 11.3, 3.7],
            [21.7, 44.0, 19.9, 10.7, 3.6],
        ],
        "S15 (好)": [
            [27.1, 41.5, 16.8, 11.7, 2.9],
            [23.0, 30.4, 12.5, 20.8, 13.1],
            [42.2, 15.3, 4.7, 18.4, 18.8],
        ],
    }
    bands = ["Delta", "Theta", "Alpha", "Beta", "Gamma"]
    task_colors = {"Relax": "#4a90e2", "Focus": "#f5a623", "Blink": "#e5484d"}

    margin_top, margin_bot = 40, 60
    panel_w = (width - 60) / 4
    panel_h = height - margin_top - margin_bot
    bar_group_w = panel_w - 30
    band_w = bar_group_w / 5

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'style="background:#fafafa;border-radius:8px;font-family:sans-serif">']
    parts.append(f'<text x="{width/2}" y="22" text-anchor="middle" '
                 f'font-size="14" font-weight="600" fill="#333">'
                 f'好/壞受試者頻帶相對功率對比</text>')

    for pi, (sub, vals) in enumerate(data.items()):
        x0 = 30 + pi * panel_w
        # 子標題
        parts.append(f'<text x="{x0 + panel_w/2}" y="{margin_top - 6}" '
                     f'text-anchor="middle" font-size="12" font-weight="600" '
                     f'fill="{"#999" if "剃除" in sub else "#333"}">{sub}</text>')
        # baseline
        for tick in (0, 25, 50, 75):
            y = margin_top + panel_h * (1 - tick/75)
            parts.append(f'<line x1="{x0+15}" x2="{x0+15+bar_group_w}" '
                         f'y1="{y}" y2="{y}" stroke="#e5e5e5" stroke-width="1"/>')
        # 三個任務 x 五個頻帶 → 每個頻帶 3 條小柱
        sub_bar_w = band_w * 0.25
        for bi, band in enumerate(bands):
            for ti, task in enumerate(("Relax", "Focus", "Blink")):
                v = vals[ti][bi]
                bx = x0 + 15 + bi * band_w + ti * sub_bar_w + 2
                bh = panel_h * (v / 75)
                by = margin_top + panel_h - bh
                parts.append(f'<rect x="{bx}" y="{by}" width="{sub_bar_w-1}" height="{bh}" '
                             f'fill="{task_colors[task]}" rx="1"/>')
            parts.append(f'<text x="{x0 + 15 + bi * band_w + band_w/2 - 4}" '
                         f'y="{height - margin_bot + 12}" text-anchor="middle" '
                         f'font-size="9" fill="#666">{band}</text>')

    # 圖例
    leg_y = height - 18
    leg_x = width / 2 - 100
    for i, (task, c) in enumerate(task_colors.items()):
        parts.append(f'<rect x="{leg_x + i*70}" y="{leg_y}" width="12" height="12" fill="{c}"/>')
        parts.append(f'<text x="{leg_x + i*70 + 16}" y="{leg_y + 10}" font-size="11" fill="#333">{task}</text>')

    parts.append('</svg>')
    return "\n".join(parts)


# ── HTML 主體 ─────────────────────────────────────────────────────────────
HTML_TPL = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>BCI 期末專題進度報告</title>
<style>
  body {{
    font-family: -apple-system, "Microsoft JhengHei", "PingFang TC", sans-serif;
    max-width: 1100px;
    margin: 0 auto;
    padding: 32px 48px 80px;
    background: #fff;
    color: #222;
    line-height: 1.7;
    font-size: 14px;
  }}
  h1 {{
    border-bottom: 3px solid #1ed760;
    padding-bottom: 12px;
    font-size: 26px;
    margin-bottom: 4px;
  }}
  .subtitle {{ color: #888; font-size: 13px; margin-bottom: 32px; }}
  h2 {{
    margin-top: 40px;
    padding: 8px 14px;
    background: linear-gradient(90deg, #1ed760, transparent);
    border-radius: 4px 0 0 4px;
    color: #fff;
    font-size: 18px;
  }}
  h3 {{
    margin-top: 24px;
    color: #333;
    font-size: 15px;
    border-left: 4px solid #1ed760;
    padding-left: 10px;
  }}
  .kpi-row {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin: 20px 0 28px;
  }}
  .kpi {{
    background: #f4f4f4;
    border-radius: 8px;
    padding: 14px;
    border-left: 4px solid #1ed760;
  }}
  .kpi .label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
  .kpi .value {{ font-size: 22px; font-weight: 700; color: #222; margin-top: 2px; }}
  .kpi .sub {{ font-size: 11px; color: #999; margin-top: 2px; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 14px 0;
    font-size: 13px;
  }}
  th, td {{
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid #e8e8e8;
  }}
  th {{
    background: #f8f8f8;
    font-weight: 600;
    color: #555;
  }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.best {{ font-weight: 700; color: #1ed760; }}
  .callout {{
    background: #fff8e1;
    border-left: 4px solid #f5a623;
    padding: 12px 16px;
    border-radius: 4px;
    margin: 16px 0;
  }}
  .callout.bad {{ background: #ffebee; border-left-color: #e5484d; }}
  .callout.good {{ background: #e8f5e9; border-left-color: #1ed760; }}
  .arch {{
    background: #1a1a1a;
    color: #ddd;
    padding: 24px;
    border-radius: 8px;
    font-family: 'Menlo', 'Consolas', monospace;
    font-size: 12.5px;
    line-height: 1.6;
    overflow-x: auto;
    white-space: pre;
  }}
  code {{
    background: #f0f0f0;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 12px;
  }}
  .gui-strip {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin: 16px 0;
  }}
  .gui-card {{
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    padding: 16px;
    background: #fafafa;
  }}
  .gui-card h4 {{ margin: 0 0 8px; font-size: 13px; }}
  ul.tight {{ margin: 8px 0; padding-left: 20px; }}
  ul.tight li {{ margin: 4px 0; }}
  .footer {{
    margin-top: 60px;
    padding-top: 20px;
    border-top: 1px solid #e8e8e8;
    color: #888;
    font-size: 12px;
  }}
</style>
</head>
<body>

<h1>🧠 BCI 期末專題進度報告</h1>
<div class="subtitle">
  專題：基於 BCI 腦波感測之情境式音樂切換與控制系統 ·
  生成時間：{date}
</div>

<!-- ─── KPI ─── -->
<div class="kpi-row">
  <div class="kpi">
    <div class="label">最佳模型 LOSO</div>
    <div class="value">83.3%</div>
    <div class="sub">Ensemble (RF+SVM+LDA)</div>
  </div>
  <div class="kpi">
    <div class="label">受試者數</div>
    <div class="value">16</div>
    <div class="sub">原 18 人，剔除 S08/S10</div>
  </div>
  <div class="kpi">
    <div class="label">總訓練片段</div>
    <div class="value">12,920</div>
    <div class="sub">4 秒視窗 / 50% 重疊</div>
  </div>
  <div class="kpi">
    <div class="label">特徵維度</div>
    <div class="value">12</div>
    <div class="sub">5 頻帶 + 3 Hjorth + std + α/β + p2p + ZCR</div>
  </div>
</div>

<!-- ─── 模型比較 ─── -->
<h2>1. 模型比較（LOSO Cross-Validation）</h2>
<p>
  使用 16 位受試者做 Leave-One-Subject-Out 交叉驗證：
  每次訓練用 15 位資料、留 1 位完全沒看過的當測試集。
  比較 5 個演算法以選出最終模型。
</p>

{model_chart}

<table>
  <thead>
    <tr>
      <th>模型</th><th class="num">平均準確率</th><th class="num">標準差</th>
      <th class="num">超過 70% 折數</th><th>說明</th>
    </tr>
  </thead>
  <tbody>
    {model_rows}
  </tbody>
</table>

<div class="callout good">
  <strong>結論：</strong>Ensemble（隨機森林 + SVM + LDA 軟投票）以 83.3% 勝出。
  雖然單一模型差距僅 1-2%，Ensemble 的穩定性最佳（標準差最低之一），
  且各受試者間表現最一致。
</div>

<!-- ─── 各受試者表現 ─── -->
<h2>2. 各受試者 LOSO 詳細結果</h2>
<p>
  下圖是 Ensemble 模型對每一位受試者的「跨人泛化」準確率。
  綠色 ≥ 85%、橘色 70-85%、紅色 &lt; 70%、灰色為已剔除受試者。
</p>

{per_subject_chart}

<h3>異常受試者排查（為什麼剔除 S08/S10）</h3>
<p>
  在原本 18 人版本中，S08 與 S10 拉低整體表現。對其原始訊號做頻帶分析後發現結構性問題：
</p>

{band_chart}

<div class="gui-strip">
  <div class="gui-card">
    <h4>S08 — Relax 與 Focus 沒分開</h4>
    <ul class="tight">
      <li>Delta、Theta、Alpha、Beta、Gamma 五個頻帶的 Relax/Focus 值幾乎一樣</li>
      <li>差異 &lt; 5%，模型自然分不出</li>
      <li>推測：受試者沒有真的進入專注狀態</li>
    </ul>
  </div>
  <div class="gui-card">
    <h4>S10 — 訊號完全失真</h4>
    <ul class="tight">
      <li>Std = 215-252（其他人 25-75）→ 大幅波動</li>
      <li>峰峰值 2000+（其他人 200-700）→ 飽和或干擾</li>
      <li>三個任務的頻帶分布幾乎一模一樣 → <strong>沒有可分性</strong></li>
      <li>推測：電極接觸不良或藍牙連線斷續</li>
    </ul>
  </div>
</div>

<div class="callout">
  <strong>剔除前後改善：</strong>
  Ensemble 平均從 75.4% → 83.3%（+7.9%），
  標準差從 0.231 → 0.140（變動更小，模型更穩定）。
</div>

<!-- ─── 混淆矩陣 ─── -->
<h2>3. Ensemble 模型混淆矩陣</h2>
<p>
  下表彙整所有 16 位受試者的 LOSO 預測結果（總計約 13,000 筆預測）。
  數字 = 樣本數，百分比 = 該類別正確分類率（recall）。
</p>

<div style="display:flex; gap:30px; align-items:flex-start; margin:20px 0;">
  <div>{cm_chart}</div>
  <div style="flex:1; padding-top:20px;">
    <h3>各類別表現</h3>
    <table>
      <thead><tr><th>類別</th><th class="num">Recall</th><th class="num">主要混淆</th></tr></thead>
      <tbody>
        {cm_rows}
      </tbody>
    </table>
    <p style="margin-top:14px; font-size:12.5px; color:#555;">
      <strong>觀察：</strong>三類別都 ≥ 73% recall。
      最大混淆是 Focus → Relax（相對較難區分專注與放鬆），
      Blink 因為訊號特徵明顯（眼動產生大幅 Delta 波），準確率最高。
    </p>
  </div>
</div>

<!-- ─── 系統架構 ─── -->
<h2>4. 即時控制系統架構</h2>
<p>
  把訓練好的模型接到 BrainLink Lite 與 YouTube Music 網頁版。
  分成 Python 後端（推論）+ Chrome 擴充（UI 和動作），中間用 WebSocket 解耦。
</p>

<div class="arch">┌─────────────────────────────────────┐         ┌──────────────────────────────────┐
│  Python 後端 (bci_server.py)        │         │  Chrome 擴充 (bci-ytm-extension) │
│                                     │         │                                  │
│  BrainLink Lite → BrainFlow         │         │  content.js (注入 YT Music 頁)   │
│       ↓ 512 Hz raw                  │         │    ├ 連 ws://localhost:8765      │
│  4s sliding window                  │  WS     │    ├ 收 {{relax,focus,blink,...}}│
│       ↓ extract_features            ├────────►│    ├ 注入浮動 overlay (機率條)   │
│  RobustScaler → Ensemble            │  JSON   │    └ 1/2/3 連眨 → click DOM 按鈕 │
│       ↓ proba                       │         │                                  │
│  smoother + blink burst counter     │         │  popup.html (擴充 icon 點開)     │
│       ↓                             │         │    ├ 開關按鈕 (on/off)           │
│  websockets.serve(localhost:8765)   │         │    ├ 連線狀態燈                  │
│       └─ 廣播 JSON 給所有 client    │         │    └ 設定門檻、視窗秒數          │
└─────────────────────────────────────┘         └──────────────────────────────────┘
                                                          ↓
                                              直接 click YT Music 頁面上的
                                              .play-pause-button / .next-button</div>

<h3>動作對應規則</h3>
<table>
  <thead><tr><th>2 秒內連續眨眼</th><th>觸發動作</th><th>實作方式</th></tr></thead>
  <tbody>
    <tr><td>1 次</td><td>播放 / 暫停</td><td>click 頁面 #play-pause-button</td></tr>
    <tr><td>2 次</td><td>下一首</td><td>click .next-button.ytmusic-player-bar</td></tr>
    <tr><td>3 次</td><td>上一首</td><td>click .previous-button.ytmusic-player-bar</td></tr>
  </tbody>
</table>

<!-- ─── GUI 設計 ─── -->
<h2>5. GUI 設計（Chrome 擴充浮動 Overlay）</h2>
<p>
  擴充功能載入後，YouTube Music 網頁右下角會出現浮動卡片，即時顯示：
</p>

<ul class="tight">
  <li><strong>連線狀態燈</strong>：綠 = 已連 Python 後端，紅 = 未連</li>
  <li><strong>三條彩色機率條</strong>：Relax（藍）/ Focus（黃）/ Blink（紅）即時跳動</li>
  <li><strong>2 秒內眨眼計數</strong>：最多三顆紅點，達 1/2/3 顆對應觸發三種動作</li>
  <li><strong>最近 3 筆觸發紀錄</strong>：時間戳 + 動作名稱</li>
  <li><strong>觸發 Toast</strong>：每次觸發動作時跳出綠色提示帶</li>
</ul>

<div class="callout good">
  📸 <strong>完整 GUI 預覽請打開：</strong><code>gui_preview.html</code>
  （獨立檔案，不需要任何 server 即可截圖）
</div>

<!-- ─── 已完成 vs 待辦 ─── -->
<h2>6. 進度總結</h2>

<div class="gui-strip">
  <div class="gui-card" style="border-left:4px solid #1ed760">
    <h4>✓ 已完成</h4>
    <ul class="tight">
      <li>18 位資料品質診斷 + 剔除 2 位異常</li>
      <li>5 個模型 LOSO 比較訓練</li>
      <li>12 維特徵工程（含新加 α/β、p2p、ZCR）</li>
      <li>最佳模型 Ensemble 序列化（bci_model.pkl）</li>
      <li>signal_sources.py（4 種輸入來源抽象）</li>
      <li>bci_server.py（WebSocket 廣播後端）</li>
      <li>probe_brainlink.py（連線探測工具）</li>
      <li>Chrome 擴充功能完整實作（manifest + content + popup + overlay）</li>
      <li>端對端測試：檔案回放 → 擴充收到 action（驗證通過）</li>
    </ul>
  </div>
  <div class="gui-card" style="border-left:4px solid #f5a623">
    <h4>⏳ 待測試</h4>
    <ul class="tight">
      <li>BrainLink Lite 實機 BrainFlow 連線（下週拿到頭環測試）</li>
      <li>真人配戴情境下眨眼判定參數微調</li>
      <li>YT Music DOM selector 實機驗證（YT 偶爾改 class 名）</li>
      <li>長時間穩定性測試（30 分鐘以上連續使用）</li>
    </ul>
  </div>
</div>

<div class="footer">
  生成時間：{date} ·
  資料集：bci_dataset_114-2（18 位受試者，每人 3 任務 × 30 試次）·
  最佳模型：bci_model.pkl（45 MB，含 RobustScaler + VotingClassifier）
</div>

</body>
</html>
"""


def main():
    headers, rows, mean, std = read_model_csv()
    cm_title, cm = read_cm_csv()

    # 模型描述
    model_desc = {
        "MLP": "多層神經網路（3 層全連接）",
        "SVM(Linear)": "線性 SVM + Calibrated 機率",
        "LDA": "線性判別分析（最快、最簡單）",
        "RandomForest": "150 棵決策樹",
        "Ensemble(RF+SVM+LDA)": "上面三個的軟投票",
    }

    model_names = headers[1:]
    best_idx = mean.index(max(mean))

    # KPI 條
    above_70 = []
    for j in range(len(model_names)):
        cnt = sum(1 for r in rows if r[1+j] >= 0.70)
        above_70.append(cnt)

    model_rows_html = ""
    for j, name in enumerate(model_names):
        is_best = (j == best_idx)
        model_rows_html += (
            f'<tr><td{" class=\"best\"" if is_best else ""}>{html.escape(name)}'
            f'{" 🏆" if is_best else ""}</td>'
            f'<td class="num{" best" if is_best else ""}">{mean[j]:.3f}</td>'
            f'<td class="num">±{std[j]:.3f}</td>'
            f'<td class="num">{above_70[j]} / 16</td>'
            f'<td>{model_desc.get(name, "")}</td></tr>'
        )

    # 模型 bar chart
    model_chart = svg_bar_chart(
        model_names, mean, std,
        title=f"5 個模型 LOSO 平均準確率（n=16）",
        baseline=0.70,
    )

    # 各受試者圖（用 best model）
    per_subject_chart = svg_per_subject_chart(rows, headers, model_idx=1+best_idx)

    # 頻帶比較圖
    band_chart = svg_band_compare()

    # 混淆矩陣
    cm_chart = svg_confusion_matrix(cm)
    labels = ("Relax", "Focus", "Blink")
    cm_rows = ""
    for i, (lbl, row) in enumerate(zip(labels, cm)):
        recall = row[i] / sum(row)
        # 主要混淆 = 對角線外最大值
        confused_idx = max([j for j in range(len(labels)) if j != i], key=lambda j: row[j])
        confused_pct = row[confused_idx] / sum(row)
        cm_rows += (
            f'<tr><td>{lbl}</td>'
            f'<td class="num"><strong>{recall*100:.1f}%</strong></td>'
            f'<td class="num">→ {labels[confused_idx]} ({confused_pct*100:.1f}%)</td></tr>'
        )

    out = HTML_TPL.format(
        date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        model_chart=model_chart,
        model_rows=model_rows_html,
        per_subject_chart=per_subject_chart,
        band_chart=band_chart,
        cm_chart=cm_chart,
        cm_rows=cm_rows,
    )

    with open("progress_report.html", "w", encoding="utf-8") as f:
        f.write(out)
    print(f"✅ 已產生 progress_report.html（{len(out)/1024:.1f} KB）")


if __name__ == "__main__":
    main()
