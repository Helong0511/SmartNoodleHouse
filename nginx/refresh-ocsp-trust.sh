#!/bin/sh
# Certbot deploy hook: rebuild the OCSP trust bundle after initial issuance/renewal.
set -eu

lineage="${RENEWED_LINEAGE:?Certbot must set RENEWED_LINEAGE}"
fullchain="${lineage}/fullchain.pem"
system_roots="/etc/ssl/certs/ca-certificates.crt"
bundle="/etc/nginx/ssl-trusted-chain.pem"

if [ ! -s "$fullchain" ]; then
    echo "Missing renewed certificate chain: $fullchain" >&2
    exit 1
fi
if [ ! -s "$system_roots" ]; then
    echo "Missing system CA bundle: $system_roots (install ca-certificates)" >&2
    exit 1
fi

tmp="$(mktemp /etc/nginx/.ssl-trusted-chain.XXXXXX)"
trap 'rm -f "$tmp"' EXIT HUP INT TERM
cat "$fullchain" "$system_roots" > "$tmp"
chown root:root "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$bundle"

nginx -t
systemctl reload nginx
