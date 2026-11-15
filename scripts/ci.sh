#!/usr/bin/env bash
# Локальный CI: те же проверки, что в .github/workflows/ci.yml (они же идут на
# self-hosted раннере на сервере). Этапы ci.yml и этого скрипта держать согласованными — меняете один, меняйте и другой.
#
#   scripts/ci.sh            всё: backend, frontend, compose-файлы Dokploy, e2e на поднятом стеке
#   scripts/ci.sh --no-e2e   без e2e (Docker нужен только для проверки compose-файлов)
#   scripts/ci.sh --fast     без e2e и без аудитов зависимостей
#
# Порты e2e-стека на 127.0.0.1: E2E_PORT (по умолчанию 28080), E2E_DB_PORT (55434).
# Запускается pre-push хуком (.githooks/pre-push) при пуше в dev и main.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAST=0
E2E=1
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1; E2E=0 ;;
    --no-e2e) E2E=0 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный флаг: $arg (есть --fast, --no-e2e)" >&2; exit 2 ;;
  esac
done

E2E_PORT="${E2E_PORT:-28080}"
E2E_DB_PORT="${E2E_DB_PORT:-55434}"
export E2E_PORT E2E_DB_PORT
# Заглушки для compose; значения из окружения shell важнее файла .env
export POSTGRES_PASSWORD=ci BACKEND_ALIAS=tinvest-backend-ci
COMPOSE=(docker compose -p tinvest-ci -f docker-compose.yml -f docker-compose.e2e.yml)

if [ -t 1 ]; then
  BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

STAGES=()
RESULTS=()
FAILED=0
START_ALL=$SECONDS

# run_stage "Название" команда... — при первом падении остальные этапы пропускаются
run_stage() {
  local name="$1"; shift
  if [ "$FAILED" -ne 0 ]; then
    STAGES+=("$name"); RESULTS+=("SKIP")
    return
  fi
  echo
  echo "${BOLD}━━━ $name ━━━${RESET}"
  local t0=$SECONDS
  "$@"
  local code=$?
  local dt=$((SECONDS - t0))
  STAGES+=("$name")
  if [ "$code" -eq 0 ]; then
    RESULTS+=("OK   ${dt}s")
  else
    RESULTS+=("FAIL ${dt}s")
    FAILED=1
    echo "${RED}✗ $name: код $code${RESET}"
  fi
}

skip_stage() {
  STAGES+=("$1"); RESULTS+=("SKIP $2")
}

summary() {
  echo
  echo "${BOLD}━━━ Итог ━━━${RESET}"
  local i color
  for i in "${!STAGES[@]}"; do
    case "${RESULTS[$i]}" in
      OK*) color=$GREEN ;;
      FAIL*) color=$RED ;;
      *) color=$YELLOW ;;
    esac
    printf '  %s%-12s%s %s\n' "$color" "${RESULTS[$i]}" "$RESET" "${STAGES[$i]}"
  done
  echo "  Всего: $((SECONDS - START_ALL))s"
  if [ "$FAILED" -eq 0 ]; then
    echo "${GREEN}${BOLD}CI пройден${RESET}"
  else
    echo "${RED}${BOLD}CI не пройден${RESET}"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then cat "$@" | sha256sum | cut -d' ' -f1
  else cat "$@" | shasum -a 256 | cut -d' ' -f1
  fi
}

# ── Backend ──────────────────────────────────────────────────────────────────

find_python() {
  local p
  for p in python3.12 python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

VENV="$ROOT/backend/.venv"
venv_bin() {
  if [ -d "$VENV/Scripts" ]; then echo "$VENV/Scripts"; else echo "$VENV/bin"; fi
}

backend_deps() {
  if [ ! -d "$VENV" ]; then
    local py
    py="$(find_python)" || { echo "Нужен Python 3.12+ (python3 / python в PATH)" >&2; return 1; }
    echo "Создаю backend/.venv ($("$py" --version))"
    "$py" -m venv "$VENV" || return 1
  fi
  local bin hash marker="$VENV/.ci-requirements.sha256"
  bin="$(venv_bin)"
  hash="$(sha256_of backend/requirements.txt backend/requirements-dev.txt)" || return 1
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$hash" ]; then
    echo "Зависимости не менялись — установка пропущена"
    return 0
  fi
  echo "pip install -r requirements-dev.txt"
  (cd backend && "$bin/python" -m pip install -q --disable-pip-version-check -r requirements-dev.txt) || return 1
  echo "$hash" > "$marker"
}

backend_audit() {
  (cd backend && "$(venv_bin)/python" -m pip_audit -r requirements.txt)
}

backend_tests() {
  # e2e без E2E_BASE_URL пропускаются сами
  (cd backend && env -u E2E_BASE_URL -u TEST_DATABASE_URL "$(venv_bin)/python" -m pytest -q)
}

DB_TESTS_STARTED=0
DB_COMPOSE=(docker compose -p tinvest-dbtests -f docker-compose.db-tests.yml)
backend_db_tests() {
  DB_TESTS_STARTED=1
  "${DB_COMPOSE[@]}" up -d --wait || return 1
  (cd backend && env -u E2E_BASE_URL TEST_DATABASE_URL="postgresql+psycopg://bonds:ci@127.0.0.1:${TEST_DB_PORT:-55435}/bonds_test" \
    "$(venv_bin)/python" -m pytest -q -m db)
  local result=$?
  "${DB_COMPOSE[@]}" down -v || result=1
  DB_TESTS_STARTED=0
  return "$result"
}

# ── Frontend ─────────────────────────────────────────────────────────────────

frontend_deps() {
  local hash marker="frontend/node_modules/.ci-package-lock.sha256"
  hash="$(sha256_of frontend/package-lock.json)" || return 1
  if [ -d frontend/node_modules ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$hash" ]; then
    echo "package-lock.json не менялся — npm ci пропущен"
    return 0
  fi
  (cd frontend && npm ci --no-fund --no-audit) || return 1
  echo "$hash" > "$marker"
}

# Найденная уязвимость (high и выше) — падение. Недоступность сервиса аудита (503, таймаут) —
# не уязвимость: 3 попытки, затем предупреждение
frontend_audit() {
  local attempt log
  log="$(mktemp)" || return 1
  for attempt in 1 2 3; do
    if (cd frontend && npm audit --audit-level=high) > "$log" 2>&1; then
      cat "$log"; rm -f "$log"; return 0
    fi
    cat "$log"
    if ! grep -qiE "audit endpoint returned an error|service unavailable|ETIMEDOUT|ECONNRESET|EAI_AGAIN" "$log"; then
      rm -f "$log"; return 1
    fi
    echo "Сервис аудита npm недоступен, попытка $attempt из 3"
    [ "$attempt" -lt 3 ] && sleep 20
  done
  rm -f "$log"
  echo "${YELLOW}ПРЕДУПРЕЖДЕНИЕ: npm audit не выполнен — сервис аудита npm недоступен${RESET}"
}

npm_run() { (cd frontend && npm run -s "$1"); }

# ── Docker ───────────────────────────────────────────────────────────────────

docker_check() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден в PATH" >&2; return 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker недоступен: запустите Docker Desktop / dockerd и проверьте права доступа (docker info)" >&2
    return 1
  fi
}

compose_config() {
  docker compose -f backend/docker-compose.yml config -q && echo "backend/docker-compose.yml: ok" &&
  docker compose -f frontend/docker-compose.yml config -q && echo "frontend/docker-compose.yml: ok"
}

check_e2e_ports() {
  # bind проверяет возможность занять порт, включая Git Bash на Windows.
  "$(venv_bin)/python" -X utf8 - <<'PY'
import contextlib
import os
import socket
import sys

ports = []
for name in ("E2E_PORT", "E2E_DB_PORT"):
    value = os.environ[name]
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        sys.exit(f"{name}: нужен номер порта от 1 до 65535, получено {value!r}")
    ports.append(int(value))
if ports[0] == ports[1]:
    sys.exit("E2E_PORT и E2E_DB_PORT должны отличаться")
with contextlib.ExitStack() as stack:
    for port in ports:
        sock = stack.enter_context(socket.socket())
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            sys.exit(
                f"Порт 127.0.0.1:{port} занят или недоступен: {error}. "
                "Задайте свободные порты: E2E_PORT=... E2E_DB_PORT=... bash scripts/ci.sh"
            )
PY
}

STACK_UP=0
stack_down() {
  if [ "$DB_TESTS_STARTED" -eq 1 ]; then
    "${DB_COMPOSE[@]}" down -v || return 1
    DB_TESTS_STARTED=0
  fi
  if [ "$STACK_UP" -eq 1 ]; then
    echo
    echo "${BOLD}━━━ Остановка e2e-стека ━━━${RESET}"
    "${COMPOSE[@]}" down -v --remove-orphans || return 1
    echo "tinvest-ci: down -v"
    STACK_UP=0
  fi
}
trap stack_down EXIT
trap 'echo; echo "Прервано"; exit 130' INT TERM

e2e_up() {
  # Остатки прошлого прерванного запуска этого же проекта
  "${COMPOSE[@]}" down -v --remove-orphans || return 1
  check_e2e_ports || return 1
  STACK_UP=1
  "${COMPOSE[@]}" build && "${COMPOSE[@]}" up -d --wait
}

e2e_tests() {
  (cd backend && E2E_BASE_URL="http://127.0.0.1:$E2E_PORT" "$(venv_bin)/python" -m pytest -q tests/e2e)
}

# ── Пайплайн ─────────────────────────────────────────────────────────────────

echo "${BOLD}Локальный CI: $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null)${RESET}"
[ "$FAST" -eq 1 ] && echo "Режим --fast: без аудитов и e2e"
[ "$FAST" -eq 0 ] && [ "$E2E" -eq 0 ] && echo "Режим --no-e2e"

run_stage "backend: зависимости" backend_deps
if [ "$FAST" -eq 0 ]; then run_stage "backend: pip-audit" backend_audit; else skip_stage "backend: pip-audit" "--fast"; fi
run_stage "backend: pytest (unit)" backend_tests

run_stage "frontend: npm ci" frontend_deps
if [ "$FAST" -eq 0 ]; then run_stage "frontend: npm audit" frontend_audit; else skip_stage "frontend: npm audit" "--fast"; fi
run_stage "frontend: lint" npm_run lint
run_stage "frontend: typecheck" npm_run typecheck
run_stage "frontend: build" npm_run build

run_stage "docker: доступен" docker_check
run_stage "compose: файлы Dokploy" compose_config
run_stage "backend: PostgreSQL tests" backend_db_tests

if [ "$E2E" -eq 1 ]; then
  run_stage "e2e: сборка и подъём стека" e2e_up
  run_stage "e2e: pytest tests/e2e" e2e_tests
  if [ "$FAILED" -ne 0 ] && [ "$STACK_UP" -eq 1 ]; then
    echo
    echo "${BOLD}━━━ Логи стека ━━━${RESET}"
    "${COMPOSE[@]}" logs --no-color --tail=200
  fi
  if [ "$STACK_UP" -eq 1 ]; then
    # Остановка обязательна и после ошибки; её собственная ошибка тоже проваливает CI.
    previous_failure=$FAILED
    FAILED=0
    run_stage "e2e: остановка стека (down -v)" stack_down
    [ "$previous_failure" -eq 0 ] || FAILED=1
  fi
else
  skip_stage "e2e" "$([ "$FAST" -eq 1 ] && echo --fast || echo --no-e2e)"
fi

summary
exit "$FAILED"
