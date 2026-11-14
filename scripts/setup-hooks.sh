#!/usr/bin/env bash
# Подключает хуки из .githooks (pre-push → scripts/ci.sh). Запустить один раз после git clone.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh
# Бит исполнения в индексе — чтобы хук работал и после clone на Linux/macOS
git update-index --chmod=+x .githooks/pre-push scripts/ci.sh scripts/setup-hooks.sh 2>/dev/null || true

echo "Готово: core.hooksPath = $(git config core.hooksPath)"
echo "Перед пушем в dev и main будет запускаться scripts/ci.sh (обход: git push --no-verify)"
