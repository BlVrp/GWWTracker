#!/usr/bin/env python3
"""
Window Focus Tracker for GNOME/Wayland

Tracks which window is in focus and logs focus changes with timestamps.
Detects AFK (Away From Keyboard) when no user input for 3 minutes.

Uses:
- AT-SPI for window focus detection (works on Wayland)
- GNOME Mutter IdleMonitor for AFK detection
"""

import gi
import time
import signal
import sys
from datetime import datetime
from pathlib import Path

gi.require_version('Atspi', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import Atspi, Gio, GLib

# Configuration
AFK_THRESHOLD_MS = 180000  # 3 minutes in milliseconds
POLL_INTERVAL_SEC = 1  # How often to check for changes
MIN_FOCUS_DURATION_SEC = 2  # Minimum duration to log (ignore rapid changes)
LOG_FILE = Path.home() / ".local/share/window-tracker/focus.log"


class WindowTracker:
    def __init__(self):
        self.current_window = None
        self.current_app = None
        self.focus_start_time = None
        self.is_afk = False
        self.afk_start_time = None
        self.running = True

        # Initialize AT-SPI
        Atspi.init()

        # Initialize D-Bus connection for IdleMonitor
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        # Ensure log directory exists
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.running = False
        self._log_focus_end(force=True)
        print("\nShutting down...")
        sys.exit(0)

    def _get_idle_time_ms(self) -> int:
        """Get current idle time in milliseconds from Mutter IdleMonitor."""
        try:
            result = self.bus.call_sync(
                'org.gnome.Mutter.IdleMonitor',
                '/org/gnome/Mutter/IdleMonitor/Core',
                'org.gnome.Mutter.IdleMonitor',
                'GetIdletime',
                None,
                GLib.VariantType.new('(t)'),
                Gio.DBusCallFlags.NONE,
                -1,
                None
            )
            return result.unpack()[0]
        except Exception as e:
            print(f"Error getting idle time: {e}", file=sys.stderr)
            return 0

    def _get_focused_window(self) -> tuple[str | None, str | None]:
        """Get the currently focused window name and application."""
        desktop = Atspi.get_desktop(0)

        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if not app:
                continue

            app_name = app.get_name()

            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if not win:
                    continue

                try:
                    states = win.get_state_set()
                    if states and states.contains(Atspi.StateType.ACTIVE):
                        win_name = win.get_name()
                        if win_name:  # Skip windows without names
                            return win_name, app_name
                except Exception:
                    continue

        return None, None

    def _format_time(self, dt: datetime) -> str:
        """Format time only (no date)."""
        return dt.strftime("%H:%M:%S")

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins:.0f}m {secs:.0f}s"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            return f"{hours:.0f}h {mins:.0f}m"

    def _log_entry(self, status: str, window: str, app: str, start_time: datetime, end_time: datetime):
        """Log a completed entry to both stdout and file."""
        duration = (end_time - start_time).total_seconds()
        duration_str = self._format_duration(duration)

        entry = f"""-----------------------
{self._format_time(start_time)}
Status: {status}
Window: {window}
App: {app}
End: {self._format_time(end_time)}
Duration: {duration_str}"""

        print(entry)
        with open(LOG_FILE, 'a') as f:
            f.write(entry + '\n')

    def _log_focus_end(self, force: bool = False):
        """Log when current window loses focus."""
        if self.current_window and self.focus_start_time:
            duration = (datetime.now() - self.focus_start_time).total_seconds()
            # Skip very short focus periods (likely title changes like spinners)
            # Unless force=True (e.g., on shutdown)
            if force or duration >= MIN_FOCUS_DURATION_SEC:
                self._log_entry(
                    status="Active",
                    window=self.current_window,
                    app=self.current_app,
                    start_time=self.focus_start_time,
                    end_time=datetime.now()
                )

    def _log_afk_end(self):
        """Log when user returns from AFK."""
        if self.afk_start_time:
            self._log_entry(
                status="AFK",
                window=self.current_window or "N/A",
                app=self.current_app or "N/A",
                start_time=self.afk_start_time,
                end_time=datetime.now()
            )

    def _check_afk_status(self):
        """Check and update AFK status."""
        idle_time = self._get_idle_time_ms()

        if idle_time >= AFK_THRESHOLD_MS and not self.is_afk:
            # User just went AFK - log the current focus period first
            self._log_focus_end()
            self.is_afk = True
            self.afk_start_time = datetime.now()
        elif idle_time < AFK_THRESHOLD_MS and self.is_afk:
            # User returned from AFK
            self._log_afk_end()
            self.is_afk = False
            self.afk_start_time = None
            # Reset focus tracking
            self.focus_start_time = datetime.now()

    def _check_focus_change(self):
        """Check for window focus changes."""
        window, app = self._get_focused_window()

        if window != self.current_window or app != self.current_app:
            # Focus changed - log the previous window
            self._log_focus_end()

            self.current_window = window
            self.current_app = app
            self.focus_start_time = datetime.now()

    def run(self):
        """Main tracking loop."""
        print("Window Focus Tracker for GNOME/Wayland")
        print("=" * 40)
        print(f"Logging to: {LOG_FILE}")
        print(f"AFK threshold: {AFK_THRESHOLD_MS // 1000 // 60} minutes")
        print("Press Ctrl+C to stop\n")

        # Initialize with current window
        self.current_window, self.current_app = self._get_focused_window()
        self.focus_start_time = datetime.now()

        while self.running:
            try:
                self._check_afk_status()

                # Only check focus if not AFK
                if not self.is_afk:
                    self._check_focus_change()

                time.sleep(POLL_INTERVAL_SEC)
            except Exception as e:
                print(f"Error in main loop: {e}", file=sys.stderr)
                time.sleep(POLL_INTERVAL_SEC)


def main():
    tracker = WindowTracker()
    tracker.run()


if __name__ == "__main__":
    main()
