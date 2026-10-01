import io
import unittest
from unittest.mock import patch

from delphi_code.progress import Progress


class ProgressLine(unittest.TestCase):
    def test_silent_without_a_terminal(self):
        with Progress(None) as progress:
            progress.begin("repo")
            progress.count("embedding", 3, 10, "files")
            progress.end("✓", "done")

    def test_counts_fit_the_terminal_and_end_on_a_kept_line(self):
        stream = io.StringIO()
        with (
            patch("delphi_code.progress.REDRAW_SECONDS", 0.001),
            patch("delphi_code.progress._columns", return_value=79),
            Progress(stream) as progress,
        ):
            progress.begin("[1/2] github.com/owner/repository")
            progress.count("embedding", 50, 10, "files")
            while "10/10 files" not in stream.getvalue():
                pass
            progress.end("✓", "10 files, 40 chunks")
        frames = [frame for frame in stream.getvalue().split("\r\033[K") if frame]
        self.assertTrue(all(len(frame.rstrip("\n")) <= 79 for frame in frames))
        self.assertTrue(any("10/10 files ██" in frame for frame in frames))
        self.assertTrue(frames[-1].startswith("✓ [1/2] github.com/owner/repository  10 files, 40 chunks"))


if __name__ == "__main__":
    unittest.main()
