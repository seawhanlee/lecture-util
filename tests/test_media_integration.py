from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from lecture_util.media import download_hls, extract_audio


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("yt-dlp"), "media tools unavailable")
class MediaIntegrationTests(unittest.TestCase):
    def test_local_hls_download_and_audio_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playlist = root / "index.m3u8"
            subprocess.run(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x90:r=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=16000",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-f",
                    "hls",
                    "-hls_time",
                    "1",
                    str(playlist),
                ],
                check=True,
            )
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                video = root / "result" / "source.mp4"
                audio = root / "result" / "audio.wav"
                download_hls(f"http://127.0.0.1:{server.server_port}/index.m3u8", video)
                extract_audio(video, audio)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            self.assertGreater(video.stat().st_size, 0)
            self.assertGreater(audio.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
