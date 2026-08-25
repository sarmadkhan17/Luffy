#!/usr/bin/env bash
# One-time TradingView login for the TV harness.
# A Chromium window opens on this VM's display — log in yourself,
# then return here and press Enter. Session persists for months.
cd "$(dirname "$0")"
exec ./venv/bin/python -m trader.brain.tv_harness --login
