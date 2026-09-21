#!/bin/bash
# Deterministic driver: 9 kernels, primary (224-bit) then replay (384-bit, distinct axis/order, reversed inner traversal).
cd "$(dirname "$0")/../.."
D=scripts/m32_p5c3_p6c4_remaining_20260921
IDS="362941837322c8d33797 5b0820e2459afe050862 82fa6cbd43d44f3d5ad8 f94178b70f5311220985 192a266e63028fd9405f 1c44894573b030f89efe 444db08366c304e754fc 63a8f6ab39aa1458349a 7d91c0980805e807c160"
mode=$1
for k in $IDS; do
  if [ "$mode" = primary ]; then A="--precision-bits 224 --initial-axis 80 --order 14"; else A="--precision-bits 384 --initial-axis 82 --order 16 --reverse-inner"; fi
  echo "./venv/bin/python $D/certify_remaining.py --kernel-id $k $A --checkpoint $D/$mode/$k.checkpoint.jsonl --output $D/$mode/$k.json > $D/logs/${mode}_$k.log 2>&1"
done | xargs -P ${2:-4} -I{} bash -c "{}"
