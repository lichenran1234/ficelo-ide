"""The `ficelo` command."""

import argparse
import os
import socket
import sys
from pathlib import Path

from . import __version__
from . import state
from .config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_ROOT, Config
from .server import serve


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ficelo",
        description="Browse, read and edit files over HTTP. Standard library only.",
        epilog="Defaults can be kept in ~/.ficelo/config.json (or $FICELO_HOME/config.json), "
        'e.g. {"port": 9248, "write": false}. Keys: root, host, port, write, hidden, terminal, allowed_origins. '
        "Flags given here override it. Page settings are saved in the same folder.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="the folder to serve (default: /, the whole filesystem — the tree "
        "still opens at your home folder)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="address to listen on (default: %s, this machine only). " % DEFAULT_HOST +
        "Use :: for every interface on IPv6 and IPv4 — 0.0.0.0 is IPv4 only, and "
        "unreachable from a machine whose name resolves to IPv6. "
        "Any address other than loopback exposes the served folder to the network, "
        "with no password.",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="port (default: %d)" % DEFAULT_PORT
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        default=None,
        help="allow changing files (the default); overrides a saved write:false setting",
    )
    mode.add_argument("--read-only", dest="write", action="store_false", default=None,
                      help="disable file changes for this server; overrides a saved write:true setting")
    parser.add_argument(
        "--hidden", action="store_true", default=None, help="show dotfiles in listings"
    )
    parser.add_argument("--terminal", action="store_true", default=None, help="enable local shell terminals (requires read-write mode)")
    parser.add_argument("--allow-origin", action="append", metavar="URL",
                        help="allow this exact browser origin when a reverse proxy rewrites Host; repeat for multiple origins")
    parser.add_argument("--version", action="version", version="ficelo " + __version__)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if sys.platform.startswith("win") or os.name == "nt":
        # Said up front rather than half-working: Windows has drives rather
        # than one "/", and the path mapping, hidden-file and save handling
        # are written for Linux and macOS only.
        print("ficelo: Windows is not supported yet; it runs on Linux and macOS.", file=sys.stderr)
        return 2

    # Command-line flags win over ~/.ficelo/config.json, which wins over the
    # built-in defaults. A broken config file stops startup with its path
    # and the problem, rather than being silently ignored.
    try:
        saved = state.read_config(state.home())
    except state.BadState as err:
        print("ficelo: %s" % err, file=sys.stderr)
        return 2

    def pick(flag, key, default):
        if flag is not None:
            return flag
        return saved.get(key, default)

    chosen_root = pick(args.root, "root", DEFAULT_ROOT)
    root = Path(chosen_root).expanduser()
    if not root.is_dir():
        # Said plainly, because the usual cause is a typo in the path and the
        # usual alternative is a traceback that buries it.
        print("ficelo: not a folder: %s" % root, file=sys.stderr)
        return 2
    if not os.access(str(root), os.R_OK | os.X_OK):
        print("ficelo: cannot read: %s" % root, file=sys.stderr)
        return 2

    writable = pick(args.write, "write", True)
    terminal = pick(args.terminal, "terminal", False)
    if terminal and not writable:
        print("ficelo: --terminal requires read-write mode; remove --read-only or override saved write:false with --write", file=sys.stderr)
        return 2

    try:
        config = Config(
            root=root,
            host=pick(args.host, "host", DEFAULT_HOST),
            port=pick(args.port, "port", DEFAULT_PORT),
            writable=writable,
            show_hidden=pick(args.hidden, "hidden", False),
            terminal=terminal,
            allowed_origins=pick(args.allow_origin, "allowed_origins", []),
        )
    except ValueError as error:
        print(f"ficelo: {error}", file=sys.stderr)
        return 2

    try:
        server = serve(config)
    except OSError as err:
        if err.errno in (98, 48):  # EADDRINUSE, and its BSD/macOS number
            print(
                "ficelo: port %d is already in use. Pick another with --port."
                % config.port,
                file=sys.stderr,
            )
            return 1
        if err.errno in (13, 1):  # EACCES, EPERM
            print(
                "ficelo: not allowed to listen on port %d. Ports below 1024 "
                "need root; pick a higher one with --port." % config.port,
                file=sys.stderr,
            )
            return 1
        if isinstance(err, socket.gaierror):
            print("ficelo: cannot listen on %s: %s" % (config.host, err), file=sys.stderr)
            return 1
        raise

    print(config.describe())
    print("  stop     Ctrl-C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is how this is meant to end, so it exits 0 and says nothing
        # alarming.
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
