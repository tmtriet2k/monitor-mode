# monitor-mode — Linux and macOS CLI

One command for switching a Samsung S34CG50 between full-screen HDMI and equal-split Picture-by-Picture (PBP). The launcher detects the operating system and uses `ddcutil` + Hyprland on Linux or your running BetterDisplay on macOS.

`full` selects **HDMI 2 on Linux** and **HDMI 1 on Mac** by default. PBP always restores HDMI 1 + HDMI 2. Run a switch from one computer at a time. Each backend configures only its own computer's desktop.

## Get the CLI

Clone the repository:

```bash
git clone https://github.com/tmtriet2k/monitor-mode.git
cd monitor-mode
```

These three files must remain **in the same directory**:

- `monitor-mode`: shared CLI launcher.
- `monitor-mode-linux`: Linux backend.
- `monitor-mode-mac`: macOS backend.

Requires **Python 3.10 or newer**. Inspect the files, then run:

```bash
chmod +x monitor-mode
./monitor-mode --help
./monitor-mode list
```

You can also run `python3 monitor-mode ...`. Only the launcher needs executable permission. No installer, dependency download, or global package installation is included. Manage additional CLI dependencies through your existing software policy; personal Nix tool installations belong in your tools flake.

`list` is read-only and prints monitor identifiers locally. Keep real serials, UUIDs, and diagnostic output out of public comments.

## Set up Linux

Requires existing `ddcutil` and `hyprctl`, with Linux on **HDMI 2**. `list` runs `ddcutil detect` and `hyprctl monitors`. Use the Samsung's serial and Hyprland output name:

```bash
export MONITOR_SERIAL='YOUR_MONITOR_SERIAL'
export MONITOR_OUTPUT='YOUR_HYPRLAND_OUTPUT'
./monitor-mode status
```

Your Hyprland setup must already load `~/.config/hypr/monitors.lua` or a path passed with `--config PATH`. That file must contain exactly one `local samsung_pbp = false` (or `true`) setting and use it to select **3440x1440** for full screen and **1720x1440** for PBP on reload.

The Linux backend was extracted from a Lua-based Hyprland setup. It does not create this configuration or adapt a standard `hyprland.conf` setup. `MONITOR_HYPR_INSTANCE` selects the Hyprland instance (default `0`). `--serial` and `--output` override their environment settings.

## Set up macOS

Requires an existing, running **BetterDisplay** with CLI integration and DDC enabled for the Samsung. Its local CLI supports Intel and Apple Silicon Macs; actual DDC availability depends on the connection. The app's bundled executable is used, so a separate `betterdisplaycli` installation is unnecessary.

Run `./monitor-mode list`, choose the Samsung's UUID from BetterDisplay's identifiers, then:

```bash
export MONITOR_MAC_DISPLAY='YOUR_DISPLAY_UUID'
./monitor-mode status
```

Replace all placeholders with your own values. Alternatively supply `--display UUID` or `--serial SERIAL` / `MONITOR_SERIAL` for the monitor's alphanumeric serial. An explicit CLI selector overrides environment settings. If both environment selectors exist, `MONITOR_SERIAL` takes precedence. No monitor is selected automatically.

Mac options:

- `--local-input hdmi2`: when the Mac is on HDMI 2. `full` follows this setting; PBP still restores HDMI 1 + HDMI 2. Linux requires its computer on HDMI 2.
- `--no-resolution`: let macOS manage resolution. Otherwise select an available 1720x1440 / 3440x1440 mode with HiDPI off; missing modes are not created.
- `--backend PATH`: another installed BetterDisplay executable or an existing `betterdisplaycli`. Also configurable with `MONITOR_MAC_BACKEND`.

Backend references: [BetterDisplay CLI reference](https://betterdisplay.pro/guide/integration/cli-reference/) and [DDC / display-mode examples](https://betterdisplay.pro/guide/integration/cli-examples/).

## Same commands on both systems

```bash
./monitor-mode status # Read monitor and desktop state; default command
./monitor-mode full   # This computer full screen
./monitor-mode pbp    # HDMI 1 + HDMI 2 split screen
./monitor-mode toggle # PBP / this computer full screen
./monitor-mode hdmi1  # HDMI 1 full screen
./monitor-mode hdmi2  # HDMI 2 full screen
./monitor-mode list   # Read available monitor identifiers
```

`--dry-run` prints a plan for `pbp`, `full`, `hdmi1`, or `hdmi2` without calling external tools or changing hardware, configuration, or locks. Supply the same selectors as for a real command. Actual switching reads state and skips redundant controls.

```bash
./monitor-mode pbp --dry-run
./monitor-mode full --dry-run
```

`--platform linux` or `--platform macos` lets you inspect another OS's dry-run plan. Actual commands for a different OS are rejected. This example uses a fictitious identifier and sends nothing:

```bash
./monitor-mode --platform macos --display 00000000-0000-0000-0000-000000000001 pbp --dry-run
```

The backends remain directly runnable for compatibility. Platform-specific options are rejected on the other OS; arguments containing spaces, exit codes, and signals are forwarded without a shell.

## Switching and recovery

Samsung-specific controls are VCP `60` for HDMI input, vendor `E2` for PBP, and vendor `E3` for the secondary pane. Other monitor models require verification and adaptation.

Linux switching updates the Lua profile and reloads Hyprland. The first profile change creates a sibling `.bak.monitor-mode` backup; a lock under `~/.local/state/monitor-mode/` prevents concurrent Linux switches. Failures attempt to restore the previous monitor state and configuration.

Mac switching reads PBP and input state, plus the desktop mode when resolution management is enabled, before sending writes. Unexpected or unreadable initial state stops the operation. Ordinary failures attempt recovery. A lock under `~/Library/Application Support/monitor-mode/` prevents concurrent Mac switches.

A Mac handoff to the other input can remove its DDC connection. It is reported as **sent**, not verified. An error on the final handoff write means the outcome is **unknown**, with no automatic rollback that could undo a successful handoff. Check the monitor menu or other computer. A hidden Mac input may be unable to switch back; use the Linux command or physical monitor menu to restore PBP.

The Mac command cannot update Linux's Hyprland profile. If Linux has the wrong resolution after a Mac-initiated switch, run `monitor-mode pbp` or `monitor-mode hdmi2` on Linux as appropriate. If recovery fails, restore PBP through the physical monitor menu before retrying.

## Validation and privacy

Run the offline tests from the repository directory:

```bash
python3 -m unittest discover -s tests -v
```

Tests use simulated monitors and do not change physical monitor or desktop settings.

CLI routing, flags, read-only behavior, dry runs, and error handling are tested on Linux. Mac switching, backend arguments, handoffs, and recovery are tested with a simulated monitor. **Mac DDC still requires testing on real hardware; that backend remains experimental.**

The repository contains the three scripts, this README, and offline tests. Real identifiers are supplied locally; no personal serial, UUID, local username, desktop configuration, diagnostic logs, or credentials are embedded. The example UUIDs are fictitious; the clone URL identifies the public GitHub account. Scripts make no network requests and use argument vectors without a shell. BetterDisplay's own settings are managed separately; HTTP integration is unnecessary.
