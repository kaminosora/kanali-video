"""Disconnected browser requests must not masquerade as service failures."""
import contextlib
import io
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


class BrokenReader(io.BytesIO):
    def __init__(self, data=b"", *, fail_body=False):
        super().__init__(data)
        self.fail_body = fail_body

    def readline(self, *args):
        if not self.fail_body and self.tell() == len(self.getvalue()):
            raise ConnectionAbortedError(10053, "browser disconnected")
        return super().readline(*args)

    def read(self, *args):
        if self.fail_body:
            raise ConnectionAbortedError(10053, "upload disconnected")
        return super().read(*args)


class BrokenWriter(io.BytesIO):
    def __init__(self, error, *, flush_only=False):
        super().__init__()
        self.error, self.flush_only, self.attempts = error, flush_only, 0

    def write(self, value):
        self.attempts += 1
        if not self.flush_only:
            raise self.error("browser disconnected")
        return super().write(value)

    def flush(self):
        if self.flush_only:
            raise self.error("browser disconnected during flush")


def handler(reader, writer):
    result = app.Handler.__new__(app.Handler)
    result.rfile, result.wfile = reader, writer
    result.client_address = ("127.0.0.1", 12345)
    result.server = SimpleNamespace(server_port=18765,
        app=SimpleNamespace(token="test-token", jobs=SimpleNamespace(list=lambda: [])))
    return result


GET = b"GET /api/jobs HTTP/1.1\r\nHost: 127.0.0.1:18765\r\n\r\n"
POST = (b"POST /unknown HTTP/1.1\r\nHost: 127.0.0.1:18765\r\n"
        b"X-Session-Token: test-token\r\nContent-Length: 2\r\n\r\n{}")


class DisconnectTests(unittest.TestCase):
    def run_handler(self, request):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                request.handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as exc:
            self.fail(f"Normal client disconnect escaped the HTTP boundary: {exc}")
        self.assertTrue(request.close_connection)

    def test_request_and_keepalive_read_disconnect_close_only_connection(self):
        for data in (b"", GET):
            with self.subTest(keepalive=bool(data)):
                writer = io.BytesIO()
                self.run_handler(handler(BrokenReader(data), writer))
                if data:
                    self.assertIn(b"200 OK", writer.getvalue())

    def test_response_disconnect_never_attempts_second_error_response(self):
        for data in (GET, POST):
            for error in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                with self.subTest(method=data[:4], error=error.__name__):
                    writer = BrokenWriter(error)
                    self.run_handler(handler(io.BytesIO(data), writer))
                    self.assertEqual(writer.attempts, 1, "Do not send JSON onto an already broken socket")

    def test_post_body_disconnect_does_not_write_error_response(self):
        writer = io.BytesIO()
        self.run_handler(handler(BrokenReader(POST, fail_body=True), writer))
        self.assertEqual(writer.getvalue(), b"")

    def test_response_flush_disconnect_is_a_closed_connection(self):
        self.run_handler(handler(io.BytesIO(GET), BrokenWriter(ConnectionAbortedError, flush_only=True)))

    def test_unrelated_transport_error_is_not_silently_swallowed(self):
        request = handler(io.BytesIO(GET), BrokenWriter(PermissionError))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(PermissionError):
            request.handle()


class ServiceLoggingTests(unittest.TestCase):
    def test_unhandled_request_error_keeps_diagnostic_traceback(self):
        server_type = getattr(app, "LocalHTTPServer", None)
        self.assertIsNotNone(server_type, "Unexpected request failures need durable diagnostics")
        server = server_type.__new__(server_type)
        with self.assertLogs("panorama.server", level="ERROR") as captured:
            try:
                raise RuntimeError("unexpected fixture bug")
            except RuntimeError:
                server.handle_error(None, ("127.0.0.1", 12345))
        self.assertIn("unexpected fixture bug", "\n".join(captured.output))
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_real_filesystem_failure_is_logged_with_traceback(self):
        request = handler(io.BytesIO(b"GET / HTTP/1.1\r\nHost: 127.0.0.1:18765\r\nConnection: close\r\n\r\n"), io.BytesIO())
        with patch.object(Path, "stat", side_effect=PermissionError("fixture access denied")):
            with self.assertLogs("panorama.server", level="ERROR") as captured:
                request.handle()
        self.assertIn("fixture access denied", "\n".join(captured.output))
        self.assertTrue(any(record.exc_info for record in captured.records))
        self.assertIn(b"400 Bad Request", request.wfile.getvalue())

    def test_service_log_is_persistent_and_bounded_by_rotation(self):
        configure = getattr(app, "setup_service_logging", None)
        self.assertTrue(callable(configure), "Service startup needs persistent rotating logs")
        base = ROOT / ".cache" / "tests-http"
        base.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("panorama.tests.http")
        with tempfile.TemporaryDirectory(dir=base) as directory, patch.object(app, "LOGGER", logger):
            try:
                path = configure(Path(directory), max_bytes=320, backup_count=2)
                for index in range(20):
                    logger.error("record-%02d %s", index, "diagnostic " * 7)
                for sink in logger.handlers:
                    sink.flush()
                files = list(Path(directory).glob("server.log*"))
                self.assertGreater(len(files), 1)
                self.assertLessEqual(len(files), 3)
                self.assertIn("record-19", path.read_text(encoding="utf-8"))
            finally:
                for sink in logger.handlers[:]:
                    logger.removeHandler(sink)
                    sink.close()


class JobLogDownloadTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".cache" / "tests-http"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / "render.log").write_bytes(b"ffmpeg diagnostic record")
        (self.directory / "movie.mp4").write_bytes(b"finished fixture")
        (self.directory / "source.mkv").write_bytes(b"private working data")

    def request(self, status, filename):
        request = handler(io.BytesIO(
            f"GET /output/job-fixture/{filename} HTTP/1.1\r\nHost: 127.0.0.1:18765\r\nConnection: close\r\n\r\n".encode()), io.BytesIO())
        request.server.app.jobs.jobs = {"job-fixture": {
            "status": status, "directory": str(self.directory),
            "output_path": str(self.directory / "movie.mp4") if status == "completed" else None}}
        request.handle()
        return request.wfile.getvalue()

    def test_render_log_download_is_available_before_success(self):
        for status in ("preparing", "rendering", "failed", "cancelled", "completed"):
            with self.subTest(status=status):
                response = self.request(status, "render.log")
                self.assertIn(b"200 OK", response)
                self.assertTrue(response.endswith(b"ffmpeg diagnostic record"))

    def test_log_exception_does_not_publish_other_files_or_paths(self):
        for status in ("failed", "completed"):
            for filename in ("source.mkv", "../render.log", "%2e%2e/render.log", "render.log/../source.mkv"):
                with self.subTest(status=status, filename=filename):
                    self.assertIn(b"400 Bad Request", self.request(status, filename))
        self.assertIn(b"400 Bad Request", self.request("cancelled", "movie.mp4"))
        self.assertTrue(self.request("completed", "movie.mp4").endswith(b"finished fixture"))


if __name__ == "__main__":
    unittest.main()
