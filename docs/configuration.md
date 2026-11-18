# Configuration

ficelo keeps launch settings in `~/.ficelo/config.json` and browser preferences in `~/.ficelo/settings.json`. Set `FICELO_HOME` to use another directory. Prefer the Settings menu for browser preferences; preserve unrelated keys when editing either file directly.

Command-line options override launch settings, which override built-in defaults. Restart the server after changing launch settings. For the current options, configuration keys, and defaults, run from the repository root:

```sh
PYTHONPATH=src python3 -m ficelo --help
```

An installed copy accepts the same options through `ficelo --help`.

## Served root and Home

The launch folder limits which files ficelo can browse and edit, including through symlinks. The Home preference only chooses a starting folder within that boundary; it does not grant access to other files. Restored sessions and explicit file links can select a different starting location.

Set Home in **Settings → General → Configure Home folder**. The path is validated when Settings closes; an invalid or unreadable folder leaves the previous preference intact. Changing this preference does not move the current file tree.

Read-only mode is chosen at launch and prevents changes to served files. Application preferences can still be saved. Optional terminals require read-write mode and run as the server's account; they are not confined to the served root.

## Links and sessions

Copy the browser address to reopen a file or folder with its session. Links use absolute filesystem paths, for example `http://localhost:9248/?path=/documents/notes.md`; the target must be inside the served root. An explicit path takes priority over the session's saved selection. Review links carry their repository and revision context automatically.

Sessions are stored by this ficelo server. A session link does not transfer files or grant access to another machine. Older hash-based links remain supported.

## File refresh

Manual refresh keeps the displayed file unchanged when its contents change on disk. The refresh button turns green when a newer version is available. The arrows indicate changes above or below the visible area, and both turn green when changes occur on both sides. If all changes are visible, only the arrow for the half with more changed content turns green; ties choose Down. The arrows update as you scroll. Changes without a readable position, such as binary files, also use Down.

Very large rewrites may highlight a broader span to keep scrolling responsive. Clicking either arrow reloads the whole file and moves to that edge. Turn off **Settings → File controls → Manual refresh** to reload unedited files automatically. Unsaved edits are preserved; an explicit reload asks before discarding them.

See [reading backgrounds](reading-backgrounds.md) for appearance settings and [background startup](startup.md) for user services.

## Keyboard shortcuts

The welcome screen lists file and tab shortcuts. They also work while an HTML preview or source editor has focus. In text fields, Alt+Left/Right and Alt+Shift+Left/Right keep their text-navigation behavior; use Alt+[/] to switch tabs while editing. Terminal control keys belong to the shell.

PDFs use the browser's built-in viewer, which handles its own keystrokes while focused. Click a ficelo tab or toolbar control before using ficelo shortcuts with a PDF open. Content embedded inside an HTML preview can also have its own keyboard handling.

## Remote access

ficelo has no built-in authentication. Keep loopback binding and use [SSH port forwarding](../README.md#use-over-ssh) for remote access. Binding to a network interface makes the service available to anyone who can reach that address. File access remains subject to the server account's operating-system permissions.

## Reverse proxies

If a reverse proxy rewrites the HTTP `Host` header, add the exact browser origin to `config.json`:

```json
{
  "allowed_origins": ["https://files.example.com"]
}
```

Merge this into the existing configuration and restart ficelo, or use `--allow-origin`. Include a non-default port when present in the browser address. Origins cannot contain credentials, paths, or wildcards; internationalized domains must use their ASCII punycode form.

This setting permits requests from the named browser origin; it does not enable CORS or provide authentication. A proxy connecting to a loopback-bound server must send a loopback `Host` header, such as `127.0.0.1:9248`. Forwarded headers are not trusted for this check.
