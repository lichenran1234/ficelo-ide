import io
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from test_server import ServerCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo.streaming import (
    CHUNK_BYTES,
    UnsatisfiableRange,
    byte_range,
    copy_bytes,
    open_regular,
)


class StreamingTest(unittest.TestCase):
    def test_ranges(self):
        for header, expected in [("bytes=2-5", (2, 5)), ("bytes=7-", (7, 9)),
                                 ("bytes=-3", (7, 9)), ("bytes=-99", (0, 9)),
                                 ("bytes=5-99", (5, 9)), ("other=0-1", None),
                                 ("bytes=0-1,4-5", None), (None, None)]:
            with self.subTest(header=header):
                self.assertEqual(byte_range(header, 10), expected)

    def test_invalid_and_empty_ranges(self):
        for header in ["bytes=10-", "bytes=8-2", "bytes=-0", "bytes=-", "bytes=a-b"]:
            with self.subTest(header=header), self.assertRaises(UnsatisfiableRange):
                byte_range(header, 10)
        with self.assertRaises(UnsatisfiableRange):
            byte_range("bytes=0-", 0)

    def test_copy_is_bounded_and_reports_truncation(self):
        class Bounded(io.BytesIO):
            def read(self, count=-1):
                if not 0 <= count <= CHUNK_BYTES:
                    raise AssertionError("unbounded read")
                return super().read(count)

        source = Bounded(b"x" * (CHUNK_BYTES * 3))
        output = io.BytesIO()
        self.assertTrue(copy_bytes(source, output, 1, CHUNK_BYTES * 2))
        self.assertEqual(len(output.getvalue()), CHUNK_BYTES * 2)
        self.assertFalse(copy_bytes(source, output, CHUNK_BYTES * 3, 1))

    def test_special_files_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            fifo = Path(folder) / "pipe"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError), open_regular(fifo):
                self.fail("opened pipe")
            with self.assertRaises(IsADirectoryError), open_regular(folder):
                self.fail("opened directory")


class RangeServerTest(ServerCase):
    def prepare(self):
        (self.root / "clip.mp4").write_bytes(b"0123456789")
        (self.root / "empty.bin").write_bytes(b"")
        (self.root / "document.html").write_text("<h1>hello</h1>")

    def request(self, route, range_header=None, **options):
        headers = options.pop("headers", {})
        if range_header:
            headers["Range"] = range_header
        request = urllib.request.Request(self.url(route), headers=headers, **options)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_native_media_ranges_and_complete_transfers(self):
        # A large download must never call Path.read_bytes(). The helper's
        # separate test checks the maximum size of each transfer read.
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
            for interval, wanted in [("bytes=2-5", b"2345"), ("bytes=7-", b"789"),
                                     ("bytes=-3", b"789")]:
                status, body, headers = self.request("/api/raw?path=/clip.mp4", interval)
                self.assertEqual((status, body), (206, wanted))
                self.assertEqual(headers["Accept-Ranges"], "bytes")
                self.assertTrue(headers["Content-Range"].endswith("/10"))
                self.assertEqual(int(headers["Content-Length"]), len(wanted))
            status, body, _ = self.request("/api/raw?path=/clip.mp4")
            self.assertEqual((status, body), (200, b"0123456789"))

    def test_unsatisfiable_and_empty_ranges(self):
        for route, size in [("/clip.mp4", 10), ("/empty.bin", 0)]:
            status, body, headers = self.request("/api/raw?path=" + route, "bytes=10-")
            self.assertEqual((status, body), (416, b""))
            self.assertEqual(headers["Content-Range"], f"bytes */{size}")
        self.assertEqual(self.request("/api/raw?path=/empty.bin")[:2], (200, b""))

    def test_head_and_if_range_fall_back_to_complete_metadata(self):
        status, body, headers = self.request("/api/raw?path=/clip.mp4", "bytes=0-2", method="HEAD")
        self.assertEqual((status, body, headers["Content-Length"]), (200, b"", "10"))
        status, body, _ = self.request("/api/raw?path=/clip.mp4", "bytes=0-2", headers={"If-Range": '"old-tag"'})
        self.assertEqual((status, body), (200, b"0123456789"))

    def test_preview_ranges_remain_sandboxed_and_download_names_are_unicode_safe(self):
        status, _, headers = self.request("/preview/document.html", "bytes=0-3")
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Security-Policy"], "sandbox allow-scripts")
        (self.root / "雪.bin").write_bytes(b"snow")
        status, body, headers = self.request("/api/raw?path=/%E9%9B%AA.bin")
        self.assertEqual((status, body), (200, b"snow"))
        self.assertIn("filename*=UTF-8''%E9%9B%AA.bin", headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
