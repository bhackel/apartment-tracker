#!/bin/bash
# Stop and remove the apartment-tracker hourly LaunchAgent.
# Does NOT delete the script, snapshots.csv, or tracker.log.

set -e

LABEL="com.bryce.apartmenttracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "Stopping $LABEL ..."
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null && echo "  stopped" || echo "  was not running"

if [ -f "$PLIST" ]; then
    rm "$PLIST"
    echo "Removed $PLIST"
else
    echo "No plist found at $PLIST"
fi

echo "Done. The hourly runner is uninstalled."
