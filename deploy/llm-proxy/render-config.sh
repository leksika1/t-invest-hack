#!/bin/sh
# Только данные ENVIRON -> YAML scalar. URL не передаётся в argv и не печатается.
set -eu
umask 077
template=${1:-/etc/mihomo/config.template.yaml}
output=${2:-/root/.config/mihomo/config.yaml}
if [ -z "${SUBSCRIPTION_URL:-}" ]; then
    printf '%s\n' 'SUBSCRIPTION_URL не задан: вставьте URL подписки в Environment LLM proxy.' >&2
    exit 1
fi
temporary="${output}.tmp"
trap 'rm -f "$temporary"' EXIT HUP INT TERM
if ! LC_ALL=C awk '
    BEGIN {
        url = ENVIRON["SUBSCRIPTION_URL"]
        if (url !~ /^https?:\/\/[^\/[:space:]]+/ || url ~ /[[:cntrl:]]/) exit 1
        quote = sprintf("%c", 39)
        gsub(quote, quote quote, url)
        marker = "__SUBSCRIPTION_URL__"
    }
    {
        position = index($0, marker)
        if (position) {
            count++
            print substr($0, 1, position - 1) quote url quote substr($0, position + length(marker))
        } else print
    }
    END { if (count != 1) exit 1 }
' "$template" > "$temporary" 2>/dev/null; then
    printf '%s\n' 'SUBSCRIPTION_URL: нужен HTTP(S) URL без управляющих символов и корректный шаблон mihomo.' >&2
    exit 1
fi
mv "$temporary" "$output"
