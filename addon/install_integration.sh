#!/usr/bin/env bash
# The app brings its Home Assistant integration and installs it by itself, without HACS.
# Runs in run.sh as root before the service drops root. Only this step writes into the Home
# Assistant folder, and only into the integration's own folder. Files are copied only when
# something changed. Then Home Assistant shows "restart required".
#   SOURCE  bundled integration     (default /integration/leapmotor_gateway)
#   TARGET  folder in Home Assistant (default /homeassistant/custom_components/leapmotor_gateway)
#   NOTIFY  command for the notification, gets title and text (default: persistent_notification)
set -u
SOURCE="${SOURCE:-/integration/leapmotor_gateway}"
TARGET="${TARGET:-/homeassistant/custom_components/leapmotor_gateway}"

notify() {
  if [ -n "${NOTIFY:-}" ]; then "$NOTIFY" "$1" "$2"; return; fi
  curl -s -m 20 -X POST -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" -H "Content-Type: application/json" \
    -d "$(jq -cn --arg t "$1" --arg m "$2" '{title: $t, message: $m, notification_id: "leapmotor_gateway_integration"}')" \
    http://supervisor/core/api/services/persistent_notification/create >/dev/null || true
}

# Checksums instead of diff: the image has the BusyBox diff, which knows no -x.
checksums() { [ -d "$1" ] && (cd "$1" && find . -type f ! -path "*/__pycache__/*" -exec sha256sum {} + | sort -k2); }

[ -d "$SOURCE" ] && [ -d "$(dirname "$(dirname "$TARGET")")" ] || exit 0       # no Home Assistant folder: nothing to do
if [ -d "$TARGET" ] && [ "$(checksums "$SOURCE")" = "$(checksums "$TARGET")" ]; then
  exit 0                                                                         # already current
fi
mkdir -p "$(dirname "$TARGET")"
rm -rf "$TARGET.new" && cp -r "$SOURCE" "$TARGET.new" || exit 1
find "$TARGET.new" -name __pycache__ -type d -prune -exec rm -rf {} +       # do not copy Python cache files
rm -rf "$TARGET" && mv "$TARGET.new" "$TARGET" || exit 1
V="$(jq -r .version "$TARGET/manifest.json" 2>/dev/null)"
echo "Integration leapmotor_gateway $V installed, Home Assistant restart required"
notify "Leapmotor Gateway" "Integration $V installed. Restart Home Assistant to load it. After the first installation, add it under Settings > Devices & services, where it appears as discovered."
