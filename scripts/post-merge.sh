#!/usr/bin/env bash
set -euo pipefail

# Replit runs this after a task merge to apply additive schema changes to the
# development database only. init_db() refuses to run in production.
python -m scripts.init_db
