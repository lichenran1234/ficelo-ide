# Start ficelo when you sign in

These optional user services keep ficelo running after the terminal closes and restart it after a failure. With these examples, open http://localhost:9248 in your browser; the service does not launch a browser.

The examples run directly from a checkout. Replace the repository and Python paths with absolute paths for your machine; `command -v python3` locates your interpreter. Service managers do not inherit your interactive shell environment. Create `~/.ficelo/` first and stop any other ficelo using the example port.

## Linux: systemd user service

Save this as `~/.ficelo/ficelo.service`:

```ini
[Unit]
Description=ficelo file browser

[Service]
WorkingDirectory=/absolute/path/to/ficelo
Environment=PYTHONPATH=src
ExecStart=/absolute/path/to/python3 -m ficelo / --host 127.0.0.1 --port 9248
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Enable the service and check its status and logs:

```sh
systemctl --user enable --now "$HOME/.ficelo/ficelo.service"
systemctl --user status ficelo
journalctl --user -u ficelo
```

Systemd creates registration symlinks in its user configuration directory; the definition stays in `~/.ficelo/`. After editing it, run `systemctl --user daemon-reload` and `systemctl --user restart ficelo`. To stop automatic startup, run `systemctl --user disable --now ficelo`.

This starts on sign-in. Running before sign-in depends on your system's user-manager policy.

## macOS: launchd agent

Save this as `~/.ficelo/org.ficelo.plist`. Replace all placeholder paths; launchd does not expand `~`, `$HOME`, or shell commands in the plist.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>org.ficelo</string>
  <key>WorkingDirectory</key><string>/absolute/path/to/ficelo</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>src</string></dict>
  <key>ProgramArguments</key>
  <array>
    <string>/absolute/path/to/python3</string>
    <string>-m</string><string>ficelo</string>
    <string>/</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>9248</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>/absolute/home/.ficelo/server.log</string>
  <key>StandardErrorPath</key><string>/absolute/home/.ficelo/server.log</string>
</dict>
</plist>
```

Register and load the agent:

```sh
mkdir -p "$HOME/Library/LaunchAgents"
plutil -lint "$HOME/.ficelo/org.ficelo.plist"
ln -s "$HOME/.ficelo/org.ficelo.plist" "$HOME/Library/LaunchAgents/org.ficelo.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.ficelo.plist"
launchctl print "gui/$(id -u)/org.ficelo"
```

Restart with `launchctl kickstart -k "gui/$(id -u)/org.ficelo"`. After editing the plist, run `launchctl bootout "gui/$(id -u)/org.ficelo"`, then bootstrap again. To stop automatic startup, boot it out and remove its symlink from `~/Library/LaunchAgents/`.

Logs are appended to `~/.ficelo/server.log`. macOS privacy controls still govern access to protected folders.

## Installed packages and launch options

For an installed package, replace Python's `-m ficelo` invocation with the absolute `ficelo` executable path and omit `WorkingDirectory` and `PYTHONPATH`. Update the service if you move the checkout or installation.

Add launch flags to the service arguments and restart it to apply changes. See [configuration](configuration.md) for read-only mode, terminals, and remote access. Keep the loopback binding when using SSH forwarding.
