#!/usr/bin/env bash
#
# Installer for GNOME Window Tracker.
#
# Runs the tracker + UI in place from this cloned repo (nothing is copied to
# system directories). It only:
#   1. checks for required dependencies
#   2. generates a per-user systemd service pointing at this repo
#   3. enables + starts the tracker
#
# Re-running is safe (idempotent).
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python3 || true)"
SERVICE_SRC="${REPO_DIR}/window-tracker.service.in"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_DST="${SERVICE_DIR}/window-tracker.service"
DESKTOP_SRC="${REPO_DIR}/window-tracker-ui.desktop.in"
LAUNCHER="${REPO_DIR}/window-tracker-ui-launch.sh"
APPLICATIONS_DIR="${HOME}/.local/share/applications"
DESKTOP_DST="${APPLICATIONS_DIR}/window-tracker-ui.desktop"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. dependency checks -------------------------------------------------
[ -n "$PYTHON" ] || die "python3 not found on PATH."

say "Checking Python dependencies (PyGObject + AT-SPI)..."
if ! "$PYTHON" - <<'PY' 2>/dev/null
import gi
gi.require_version("Atspi", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Atspi, Gio, GLib  # noqa: F401
PY
then
    warn "Could not import the GNOME bindings the tracker needs."
    cat <<'EOF'

  Install them with your distro's package manager, e.g.:

    Debian/Ubuntu:  sudo apt install python3-gi gir1.2-atspi-2.0
    Fedora:         sudo dnf install python3-gobject at-spi2-core
    Arch:           sudo pacman -S python-gobject at-spi2-core

  Then re-run ./install.sh
EOF
    die "Missing dependencies."
fi
say "Dependencies OK."

# --- 2. generate the systemd unit ----------------------------------------
say "Generating systemd user service..."
mkdir -p "$SERVICE_DIR"
sed -e "s|__PYTHON__|${PYTHON}|g" \
    -e "s|__SCRIPT__|${REPO_DIR}/window-tracker.py|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"
say "Wrote ${SERVICE_DST}"

# --- 3. enable + (re)start ------------------------------------------------
say "Enabling and starting the tracker service..."
systemctl --user daemon-reload
systemctl --user enable --now window-tracker.service

# Keep the service alive after logout (optional but recommended).
if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; tracker will stop when you log out."
fi

chmod +x "$LAUNCHER"

# --- 4. install the dashboard desktop entry -------------------------------
say "Installing the dashboard launcher (application menu entry)..."
mkdir -p "$APPLICATIONS_DIR"
sed -e "s|__LAUNCHER__|${LAUNCHER}|g" \
    -e "s|__ICON__|${REPO_DIR}/assets/logo.png|g" \
    "$DESKTOP_SRC" > "$DESKTOP_DST"
say "Wrote ${DESKTOP_DST}"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi

cat <<EOF

$(say "Done!")
  The tracker is now running and will start automatically on login.

  Check status:   systemctl --user status window-tracker.service
  View the log:   journalctl --user -u window-tracker.service -f
  Open the UI:    ${LAUNCHER}
                  (or search "Window Tracker" in your app menu)

  Data is stored at: ~/.local/share/window-tracker/focus.log
EOF
