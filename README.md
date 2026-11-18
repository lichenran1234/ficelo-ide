<div align="center">
<pre>
 ______   _____    _____   ______   _         ____&#32;&#32;
|  ____| |_   _|  / ____| |  ____| | |       / __ \&#32;
| |__      | |   | |      | |__    | |      | |  | |
|  __|     | |   | |      |  __|   | |      | |  | |
| |       _| |_  | |____  | |____  | |____  | |__| |
|_|      |_____|  \_____| |______| |______|  \____/&#32;
</pre>
</div>

**Ficelo — a web IDE for the agentic era. Seamlessly collaborate with coding agents on 10k+ files.**

- **On your dev machine**, where your agents run: deploy Ficelo.
- **On your laptop or desktop:** open your browser to view and edit files created by your agents on the dev machine, including Markdown, code, HTML, and more.

Runs on **Linux and macOS with Python 3.9+**. No third-party Python runtime dependencies or frontend build; bundled libraries work offline.

## Quick start

From the repository root, run without installing or creating a venv:

```sh
PYTHONPATH=src python3 -m ficelo
```

Open **[http://localhost:9248](http://localhost:9248)**. Stop with `Ctrl+C`.

Alternatively, install the `ficelo` command in a venv:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
ficelo
```

ficelo serves `/` with editing enabled unless configured otherwise. Use `PYTHONPATH=src python3 -m ficelo ~/projects --read-only` to restrict access to a folder, or `--help` for launch options. Configuration lives in `~/.ficelo/`.

## Use over SSH

Start ficelo on the remote machine with its loopback binding, then run locally:

```sh
ssh -N -L 9248:127.0.0.1:9248 user@remote-host
```

Open the same local URL. ficelo has no built-in authentication; use SSH forwarding for remote access.

## Guides

- [Configuration and reverse proxies](docs/configuration.md)
- [Reading backgrounds](docs/reading-backgrounds.md)
- [Start on sign-in](docs/startup.md)
- [Maintaining bundled libraries](docs/vendoring.md)
- [Contributing and local tests](AGENTS.md)

## License

[MIT](LICENSE). Bundled libraries retain their own licenses; see [NOTICE](NOTICE).
