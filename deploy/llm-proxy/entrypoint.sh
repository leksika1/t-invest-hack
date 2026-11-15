#!/bin/sh
set -eu
umask 077
work_dir=/root/.config/mihomo
mkdir -p "$work_dir/providers"
/bin/sh /opt/llm-proxy/render-config.sh /etc/mihomo/config.template.yaml "$work_dir/config.yaml"
# Не оставлять подписку в окружении дочернего mihomo.
unset SUBSCRIPTION_URL
# Ошибки самого mihomo могут содержать URL/token провайдера: не отдавать их в логи.
if ! /mihomo -t -d "$work_dir" -f "$work_dir/config.yaml" >/dev/null 2>&1; then
    printf '%s\n' 'Конфигурация mihomo не прошла проверку; проверьте шаблон и формат подписки.' >&2
    exit 1
fi
printf '%s\n' 'Запуск LLM proxy; URL подписки и вывод mihomo скрыты.'
exec /mihomo -d "$work_dir" -f "$work_dir/config.yaml" >/dev/null 2>&1
