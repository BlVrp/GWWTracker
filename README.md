# GNOME Window Tracker

A tiny, dependency-light activity tracker for **GNOME on Wayland**. It logs
which window has focus and for how long, detects when you're away from the
keyboard, and ships a local web dashboard to visualize your day.

No accounts, no cloud, no telemetry — everything stays in a plain text log on
your own machine.

![The dashboard: a vertical timeline of focus blocks beside Top Apps and Top Windows breakdowns](docs/screenshot.png)

## Why this exists

I wanted automatic time tracking on Linux, but the tools I knew —
[RescueTime](https://www.rescuetime.com/), [ActivityWatch](https://activitywatch.net/),
[Clockify](https://clockify.me/) — either don't support GNOME on Wayland properly,
lean on a cloud account, or were more setup than I felt like doing for what should
be a tiny job. So I built my own with Claude: a couple of standard-library Python
files, a per-user systemd service, and a local web dashboard. Nothing to sign up
for, nothing to keep running in the background but one small process.

It started as a personal convenience utility. Over time I figured it might be
useful to other people in the same spot, so here's the public repo. I'll keep
improving it — mostly for my own use, and partly for the fun of it.

To be clear, the tools above are mature, well-supported, and far more capable
than this little utility — if your setup works with them, or you want features
like cross-device sync, reporting, or team tracking, you'll be better served by
[ActivityWatch](https://activitywatch.net/) (open source, local-first),
[RescueTime](https://www.rescuetime.com/), or [Clockify](https://clockify.me/).
This project is for the narrow case where you just want lightweight, no-account,
GNOME/Wayland-native focus tracking and nothing more.

## Features

- **Focus logging** — records the active app + window title and the duration of
  each focus period.
- **AFK detection** — uses GNOME's Mutter idle monitor; after 3 minutes of no
  input you're marked Away From Keyboard.
- **Local web UI** — a vertical, zoomable timeline plus "Top Apps" / "Top
  Windows" breakdowns, served from `127.0.0.1` only.
- **Runs as a user service** — starts on login, restarts on failure, no root
  required.

## Requirements

- A GNOME desktop running on **Wayland** (uses AT-SPI for focus detection and
  the Mutter `IdleMonitor` D-Bus interface for AFK).
- `python3` (standard library only for the UI).
- PyGObject + AT-SPI bindings for the tracker:

  | Distro          | Install command                                         |
  | --------------- | ------------------------------------------------------- |
  | Debian / Ubuntu | `sudo apt install python3-gi gir1.2-atspi-2.0`          |
  | Fedora          | `sudo dnf install python3-gobject at-spi2-core`         |
  | Arch            | `sudo pacman -S python-gobject at-spi2-core`            |

## Quick start

```bash
git clone https://github.com/<you>/gnome-window-tracker.git
cd gnome-window-tracker
./install.sh
```

`install.sh` checks your dependencies, installs a **per-user systemd service**
pointing at this folder, and starts tracking immediately. Nothing is copied
into system directories — the code runs in place from the clone.

Then open the dashboard:

```bash
./window-tracker-ui-launch.sh
```

It starts the UI server (if it isn't already running) and opens
<http://localhost:8765> in your browser.

## Usage

### Service control

```bash
systemctl --user status  window-tracker.service   # is it running?
systemctl --user restart window-tracker.service   # restart
systemctl --user stop    window-tracker.service   # pause tracking
systemctl --user disable --now window-tracker.service  # turn off entirely
journalctl --user -u window-tracker.service -f     # follow the log
```

### The dashboard

- **← / →** arrow keys move between days.
- **Ctrl + scroll**, or the `−` / `1×` / `+` buttons, zoom the timeline.
- Hover any block for the app, window title, and exact time range.

### Running pieces manually (without the service)

```bash
python3 window-tracker.py                 # tracker in the foreground
python3 window-tracker-ui.py --port 8765  # UI server only
```

## Data & privacy

Your activity log lives at:

```
~/.local/share/window-tracker/focus.log
```

It is a human-readable text file and never leaves your machine. The UI binds to
`127.0.0.1` only. Delete the file to wipe your history. **Don't commit it** —
it's already in `.gitignore`.

## How it works

- `window-tracker.py` polls once per second. It asks AT-SPI for the active
  window and Mutter's `IdleMonitor` for idle time, then appends a block to the
  log whenever focus changes or you cross the AFK threshold.
- `window-tracker-ui.py` is a zero-dependency `http.server` that parses the log,
  aggregates per day, and serves a single self-contained HTML page.

### Note on AFK accounting

The 3-minute idle grace period is credited to the **active** window, not to AFK
— the AFK clock only starts once the threshold has already elapsed. So "Active"
time can be over-counted by up to ~3 minutes per idle episode. This is
intentional for now; see `_check_afk_status` in `window-tracker.py` if you want
to change it.

## Configuration

Edit the constants near the top of `window-tracker.py`:

| Constant                  | Default | Meaning                                    |
| ------------------------- | ------- | ------------------------------------------ |
| `AFK_THRESHOLD_MS`        | 180000  | Idle time before you're marked AFK (ms).   |
| `POLL_INTERVAL_SEC`       | 1       | How often to sample focus + idle state.    |
| `MIN_FOCUS_DURATION_SEC`  | 2       | Ignore focus blips shorter than this.      |

The UI port can be overridden: `WINDOW_TRACKER_UI_PORT=9000 ./window-tracker-ui-launch.sh`.

## Development

After `install.sh`, both the systemd service and the desktop launcher point at
your clone, so this folder is the single source of truth — edit in place and
reload the relevant piece to see your change:

**Tracker** (`window-tracker.py`) — restart the service and watch its log:

```bash
systemctl --user restart window-tracker.service
journalctl --user -u window-tracker.service -f
```

**UI** (`window-tracker-ui.py`) — the dashboard HTML/CSS/JS is baked into the
running Python process, so a browser refresh alone won't pick up changes. Stop
the server, relaunch, then hard-refresh the page (Ctrl+Shift+R):

```bash
pkill -f window-tracker-ui.py
./window-tracker-ui-launch.sh
```

Both files are self-contained standard-library Python — there's no build step,
bundler, or virtualenv to manage.

## License

MIT — see [LICENSE](LICENSE).
