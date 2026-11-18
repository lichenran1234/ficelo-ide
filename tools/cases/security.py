# The cross-site guards. Each of these was an exploit demonstrated against
# ficelo before the guard existed, so each must stay caught.
S = "src/ficelo/server.py"
M = "tests.test_server"

CASES = [
    (S, "the Host check is never run (DNS rebinding reads files)",
     "            self.check_host()\n", "", M),
    (S, "any Host is accepted even on loopback",
     "        if name not in LOOPBACK_NAMES and not name.endswith(\".localhost\"):",
     "        if False:", M),
    (S, "the Host check runs even when exposed (breaks proxied access)",
     "        if self.config.reachable_beyond_this_machine:\n            return\n", "", M),
    (S, "POSTs are not checked at all (cross-site writes)",
     "            if self.command == \"POST\":\n                self.check_post()\n", "", M),
    (S, "a text/plain POST is accepted",
     "        if kind != \"application/json\":", "        if False:", M),
    (S, "a foreign Origin is accepted",
     '            if (urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower()\n                    and normalised not in self.config.allowed_origins):',
     '            if False:', M),
    (S, "Sec-Fetch-Site: cross-site is ignored",
     "        if (self.headers.get(\"Sec-Fetch-Site\") or \"\") == \"cross-site\":", "        if False:", M),
]

CASES += [
    (S, "request bodies are not read before handling (proxied pages never load)",
     "            self.read_body()\n", "            self.raw_body = b\"\"\n", M),
    (S, "chunked bodies are ignored",
     '        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():',
     "        if False:", M),
    (S, "chunk trailers are left on the connection",
     '            if line in (b"\\r\\n", b"\\n", b""):\n                break\n        return b"".join(parts)',
     '            break\n        return b"".join(parts)', M),
]
