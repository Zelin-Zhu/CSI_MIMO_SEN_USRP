#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="${1:-data/captures/one_shot_$(date +%Y%m%d_%H%M%S)}"
TX_CHAIN_MODE="${2:-tx0_only}"
CAPTURE_SECONDS="${3:-0.2}"
TX_READY_TIMEOUT_SECONDS="${TX_READY_TIMEOUT_SECONDS:-20}"
TX_WARMUP_SECONDS="${TX_WARMUP_SECONDS:-0.2}"
TX_DEBUG_FRAMES="${TX_DEBUG_FRAMES:-64}"

mkdir -p "$OUT_DIR"
TX_LOG="$OUT_DIR/tx.log"
RX_LOG="$OUT_DIR/rx.log"
TX_PID=""

cleanup() {
  if [[ -n "$TX_PID" ]] && kill -0 "$TX_PID" 2>/dev/null; then
    echo "Stopping TX process $TX_PID..."
    kill "$TX_PID" 2>/dev/null || true
    wait "$TX_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

case "$TX_CHAIN_MODE" in
  both|tx0_only|tx1_only) ;;
  *)
    echo "Invalid tx_chain_mode: $TX_CHAIN_MODE"
    echo "Use one of: both, tx0_only, tx1_only"
    exit 2
    ;;
esac

echo "Output directory: $OUT_DIR"
echo "Starting TX first: tx_chain_mode=$TX_CHAIN_MODE"
python3 -u 02_iq_capture/tx_mimo_probe.py \
  --tx-chain-mode "$TX_CHAIN_MODE" \
  --debug-out-dir "$OUT_DIR" \
  --debug-frames "$TX_DEBUG_FRAMES" \
  >"$TX_LOG" 2>&1 &
TX_PID="$!"

deadline=$((SECONDS + TX_READY_TIMEOUT_SECONDS))
while true; do
  if ! kill -0 "$TX_PID" 2>/dev/null; then
    echo "TX process exited before it became ready. TX log:"
    cat "$TX_LOG"
    exit 1
  fi
  if grep -q "Transmitting. Press Ctrl+C to stop." "$TX_LOG"; then
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "Timed out waiting for TX ready line after ${TX_READY_TIMEOUT_SECONDS}s. Recent TX log:"
    tail -n 80 "$TX_LOG"
    exit 1
  fi
  sleep 0.1
done

echo "TX is transmitting. Waiting ${TX_WARMUP_SECONDS}s before RX capture..."
sleep "$TX_WARMUP_SECONDS"

echo "Starting RX capture: duration=${CAPTURE_SECONDS}s"
python3 -u 02_iq_capture/rx_capture_2ch.py \
  --seconds "$CAPTURE_SECONDS" \
  --out-dir "$OUT_DIR" \
  --tx-chain-mode "$TX_CHAIN_MODE" \
  >"$RX_LOG" 2>&1

cat "$RX_LOG"
cleanup
trap - EXIT INT TERM

echo "One-shot capture complete."
echo "TX log: $TX_LOG"
echo "RX log: $RX_LOG"
echo "Raw IQ: $OUT_DIR/rx0.fc32 and $OUT_DIR/rx1.fc32"
echo "TX debug IQ: $OUT_DIR/tx_debug_tx0.fc32 and $OUT_DIR/tx_debug_tx1.fc32"
echo "Next extraction command:"
echo "python3 03_csi_extraction/extract_csi_wifi_like.py --capture-dir $OUT_DIR --out-dir results/$(basename "$OUT_DIR")/wifi_like_debug"
