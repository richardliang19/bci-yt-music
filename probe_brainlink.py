"""
BrainLink Lite 連線探測（直接走 pyserial + TGAM 協定，不用 BrainFlow）
─────────────────────────────────────────────────────────────
1. 列出所有 COM port
2. 對每個 port 試開啟、讀 5 秒 TGAM 串流
3. 若成功 → 印出收到的 raw EEG 樣本數、振幅範圍

執行：
  python -X utf8 -u probe_brainlink.py
  python -X utf8 -u probe_brainlink.py --port COM3   # 只測指定 port
"""

import argparse
import sys
import time
import numpy as np

from signal_sources import BrainLinkSerialSource


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        sys.exit("缺少 pyserial：python -m pip install pyserial --user")
    ports = list(lp.comports())
    if not ports:
        print("找不到任何 COM port。請確認 BrainLink 已開機並完成藍牙配對。")
    return ports


def try_port(port, seconds=5):
    print(f"\n→ 嘗試 {port}（pyserial + TGAM）…")
    try:
        src = BrainLinkSerialSource(port, fs=512)
    except SystemExit as e:
        print(f"  ✗ {e}")
        return False
    except Exception as e:
        print(f"  ✗ 開啟失敗：{e}")
        return False

    all_samples = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        new, _ = src.read_new()
        if len(new):
            all_samples.append(new)
        time.sleep(0.1)

    stats = src.stats()
    src.close()

    if not all_samples:
        print(f"  ✗ {seconds}s 內沒收到任何 raw wave 樣本")
        print(f"    解析統計：good={stats['good_packets']} bad={stats['bad_packets']}")
        if stats['good_packets'] == 0 and stats['bad_packets'] == 0:
            print(f"    → 完全沒讀到 byte，可能 baud rate 錯或裝置沒在送")
        elif stats['bad_packets'] > stats['good_packets']:
            print(f"    → packet 大多無效，可能 baud rate 錯（試 9600）或不是 TGAM 協定裝置")
        return False

    eeg = np.concatenate(all_samples)
    print(f"  ✓ 成功！")
    print(f"    收到樣本數：{len(eeg)}（理論 {512*seconds}）")
    print(f"    實際取樣率：~{len(eeg)/seconds:.0f} Hz")
    print(f"    振幅範圍：[{eeg.min():.0f}, {eeg.max():.0f}]")
    print(f"    平均：{np.mean(eeg):.1f}　標準差：{np.std(eeg):.1f}")
    print(f"    解析統計：good={stats['good_packets']} bad={stats['bad_packets']}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="只測這個 port（如 COM3）")
    ap.add_argument("--seconds", type=int, default=5)
    args = ap.parse_args()

    if args.port:
        ports_to_try = [args.port]
        print(f"指定測試 port：{args.port}")
    else:
        print("掃描 COM port…")
        ports = list_ports()
        for p in ports:
            print(f"  {p.device}　{p.description}")
        ports_to_try = [p.device for p in ports]

    if not ports_to_try:
        sys.exit(1)

    success = []
    for port in ports_to_try:
        if try_port(port, args.seconds):
            success.append(port)

    print("\n" + "=" * 50)
    if success:
        print(f"✓ 可用的 BrainLink port：{success}")
        print(f"\n下一步：")
        print(f"  python -X utf8 -u bci_server.py --source brainlink:{success[0]}")
    else:
        print("✗ 沒有可用的 port。常見原因：")
        print("  1. BrainLink 沒開機 / 沒戴上（會自動省電關機）")
        print("  2. Windows 藍牙配對失敗 → 重新配對")
        print("  3. brainlink.exe 還開著佔用 port → 關掉它")
        print("  4. BrainLink 有兩個 COM port（incoming/outgoing），")
        print("     有的型號要用 outgoing 那個（通常編號較大）")


if __name__ == "__main__":
    main()
