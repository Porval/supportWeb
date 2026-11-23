#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")"
mysql_root="${ACTIVITY_DATA_DIR:-$HOME/data}/mysql-dist/mysql-8.4.13-linux-glibc2.28-x86_64-minimal"
export LD_LIBRARY_PATH="$mysql_root/lib/private:$mysql_root/lib"
export ACTIVITY_AGENT_MYSQL="$mysql_root/bin/mysql"
if [ -z "${SSL_CERT_FILE:-}" ] && [ -r /etc/pki/tls/certs/ca-bundle.crt ]; then
    export SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
fi
python3 -B mysql_runtime.py
exec python3 -B gateway.py
