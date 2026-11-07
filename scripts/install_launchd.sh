#!/bin/bash
# Install (or uninstall) the nightly launchd schedule.
#   ./scripts/install_launchd.sh            -> daily at 02:00
#   ./scripts/install_launchd.sh 3 30       -> daily at 03:30
#   ./scripts/install_launchd.sh --uninstall
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.niche-discovery.overnight"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Uninstalled ${PLIST_NAME}"
    exit 0
fi

HOUR="${1:-2}"
MINUTE="${2:-0}"

for value in "$HOUR" "$MINUTE"; do
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "Error: hour/minute must be numeric (got: '$value')" >&2
        exit 1
    fi
done
if [ "$HOUR" -gt 23 ]; then
    echo "Error: hour must be 0-23 (got: $HOUR)" >&2
    exit 1
fi
if [ "$MINUTE" -gt 59 ]; then
    echo "Error: minute must be 0-59 (got: $MINUTE)" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${PROJECT_DIR}/run_overnight.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/launchd.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "Installed ${PLIST_NAME} — nightly at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "Plist: ${PLIST_PATH}"
echo "Logs:  ${PROJECT_DIR}/logs/"
echo "Test now: launchctl start ${PLIST_NAME}"