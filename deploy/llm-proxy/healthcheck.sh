#!/bin/sh
# BusyBox cat/awk входят в Alpine-образ. 1ED2 = 7890, 0A = TCP LISTEN.
set -eu
if [ "$#" -eq 0 ]; then
    set -- /proc/net/tcp /proc/net/tcp6
fi
cat "$@" 2>/dev/null | awk '$2 ~ /:1ED2$/ && $4 == "0A" {found=1} END {exit !found}'
