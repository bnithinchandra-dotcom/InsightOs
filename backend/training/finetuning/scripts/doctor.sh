#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m insightos_training.cli hardware
