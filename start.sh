#!/bin/bash
set -eu
umask 077
cd -- "$(dirname -- "$0")"
exec python3 -B -u combined_start.py
