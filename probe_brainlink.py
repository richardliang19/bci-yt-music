"""
BrainLink Lite 連線探測
─────────────────────────────────────────────────────────────
1. 列出所有 COM port
2. 對每個 port 試 BrainFlow NeuroSky board 開 5 秒 stream
3. 若成功 → 印出取樣率、樣本數、振幅範圍

執行：
  python -X utf8 -u probe_brainlink.py
  python -X utf8 -u probe_brainlink.py --port COM3   # 只測指定 port
"""

import argparse
import sys
import time
import numpy as np


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
    try:
        from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
        from brainflow.exit_codes import BrainFlowError
    except ImportError:
        sys.exit("缺少 brainflow：python -m pip install brainflow --user")

    BoardShim.disable_board_logger()
    params = BrainFlowInputParams()
    params.serial_port = port
    bid = BoardIds.NEUROSKY_BOARD.value

    print(f"\n→ 嘗試 {port}（NEUROSKY_BOARD, id={bid}）…")
    board = BoardShim(bid, params)
    try:
        board.prepare_session()
        board.start_stream()
        time.sleep(seconds)
        data = board.get_board_data()
        board.stop_stream()
        board.release_session()
    except BrainFlowError as e:
        print(f"  ✗ 失敗：{e}")
        return False

    if data.size == 0:
        print(f"  ✗ 連線成功但 {seconds}s 內沒收到資料")
        return False

    eeg_ch = BoardShim.get_eeg_channels(bid)[0]
    fs = BoardShim.get_sampling_rate(bid)
    eeg = data[eeg_ch]
    print(f"  ✓ 成功！")
    print(f"    取樣率（驅動回報）：{fs} Hz")
    print(f"    收到樣本數：{len(eeg)}（理論 {fs*seconds}）")
    print(f"    振幅範圍：[{eeg.min():.1f}, {eeg.max():.1f}]")
    print(f"    平均：{np.mean(eeg):.1f}　標準差：{np.std(eeg):.1f}")
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
        print(f"  python -X utf8 -u bci_server.py --source brainflow:{success[0]}")
    else:
        print("✗ 沒有可用的 port。常見原因：")
        print("  1. BrainLink 沒開機 / 沒戴上（會自動省電關機）")
        print("  2. 沒在 Windows 設定完成藍牙配對（先去藍牙設定找到 BrainLink_Lite 配對）")
        print("  3. 配對成功但 COM port 沒出現 → 重新配對")
        print("  4. brainlink.exe 還開著佔用 port → 關掉它")


if __name__ == "__main__":
    main()
