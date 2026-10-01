import io
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from delphi_code.errors import ExitCode, Failure
from delphi_code.progress import Stage
from delphi_code.ui.terminal import StatusLine, TerminalProgress


class StatusLines(unittest.TestCase):
    def test_silent_without_a_terminal(self):
        with StatusLine(None) as progress:
            progress.begin("repo")
            progress.count("embedding", 3, 10, "files")
            progress.end("✓", "done")

    def test_counts_fit_the_terminal_and_end_on_a_kept_line(self):
        stream = io.StringIO()
        with (
            patch("delphi_code.ui.terminal.REDRAW_SECONDS", 0.001),
            patch("delphi_code.ui.terminal._columns", return_value=79),
            StatusLine(stream) as progress,
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


class RepositoryLines(unittest.TestCase):
    def setUp(self):
        self.line = Mock(spec=StatusLine)
        self.progress = TerminalProgress(self.line)

    def test_numbers_repositories_only_when_there_are_several(self):
        self.progress.repository_started("github.com/owner/one", 1, 1)
        self.progress.repository_started("github.com/owner/two", 2, 3)
        self.assertEqual(
            self.line.begin.call_args_list, [call("github.com/owner/one"), call("[2/3] github.com/owner/two")]
        )

    def test_stages_counts_and_outcomes(self):
        self.progress.stage_started(Stage.CLONING)
        self.progress.files_embedded(4, 9)
        self.progress.repository_finished({"unchanged": True})
        self.progress.repository_finished({"unchanged": False, "files": 9, "chunks": 30})
        self.progress.repository_failed(Failure("remote_unavailable", "git failed", ExitCode.OPERATION))
        self.line.stage.assert_called_once_with("cloning")
        self.line.count.assert_called_once_with("embedding", 4, 9, "files")
        self.assertEqual(
            self.line.end.call_args_list,
            [call("✓", "unchanged"), call("✓", "9 files, 30 chunks"), call("✗", "git failed")],
        )

    def test_listing_lines_are_cleared_not_kept(self):
        self.progress.repositories_listing_started("GitHub", "octo")
        self.progress.listing_finished()
        self.line.begin.assert_called_once_with("Listing GitHub repositories in octo")
        self.line.clear.assert_called_once_with()
        self.line.end.assert_not_called()

    def test_model_setup_lines(self):
        self.progress.model_setup_started(Path("/models/minilm"))
        self.progress.model_setup_finished(reused=True)
        self.line.begin.assert_called_once_with("model minilm")
        self.line.end.assert_called_once_with("✓", "already installed")


if __name__ == "__main__":
    unittest.main()
