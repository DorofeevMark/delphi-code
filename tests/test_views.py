from pathlib import Path
import unittest

from delphi_code.ui.text_layout import TextStyle
from delphi_code.ui.views.indexing import IndexingOutcomesSummaryView, IndexingOutcomeView
from delphi_code.ui.views.project_table import ProjectTableView
from delphi_code.ui.views.search_hits import SearchHitsView
from delphi_code.ui.views.tracking import UntrackedProjectView

UNCOLORED = TextStyle(colors_enabled=False)
HIT = {"path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "score": 0.4567, "text": "x"}
INDEXED_REPOSITORY = {
    "key": "github.com/octo/tools",
    "project": None,
    "files": 9,
    "chunks": 30,
    "incremental": {"num_adds": 2, "num_unchanged": 7},
    "index_directory": "/indexes/1",
}


class SearchHits(unittest.TestCase):
    def test_shows_location_score_and_link(self):
        hit = {**HIT, "url": "https://github.com/octo/tools/blob/abc/a.py#L1-L2"}
        view = SearchHitsView("parse config", "github.com/octo/tools", [hit], names_project_of_each_hit=False)
        self.assertEqual(
            view.lines(UNCOLORED),
            [
                '1 match for "parse config" in github.com/octo/tools',
                "",
                "a.py:1-2  python  score 0.46",
                "  https://github.com/octo/tools/blob/abc/a.py#L1-L2",
                "    x",
            ],
        )

    def test_names_the_project_of_each_hit_by_its_directory(self):
        hit = {**HIT, "key": f"local:{Path.home() / 'src' / 'api'}"}
        view = SearchHitsView("q", "2 indexes", [hit], names_project_of_each_hit=True)
        self.assertEqual(view.lines(UNCOLORED)[3], "  ~/src/api")

    def test_says_when_nothing_matches(self):
        view = SearchHitsView("q", "github.com/octo/tools", [], names_project_of_each_hit=False)
        self.assertEqual(view.lines(UNCOLORED), ['No matches for "q" in github.com/octo/tools'])


class Indexing(unittest.TestCase):
    def test_outcome_summarises_changed_files(self):
        lines = IndexingOutcomeView(INDEXED_REPOSITORY, shows_headline=True).lines(UNCOLORED)
        self.assertEqual(lines[0], "✓ Indexed github.com/octo/tools: 9 files, 30 chunks (2 added, 7 unchanged)")
        self.assertIn("  Changes  2 added, 7 unchanged", lines)

    def test_outcome_can_leave_the_headline_to_progress_lines(self):
        lines = IndexingOutcomeView(INDEXED_REPOSITORY, shows_headline=False).lines(UNCOLORED)
        self.assertTrue(lines[0].startswith("  Changes"))

    def test_summary_counts_results_and_lists_failures(self):
        outcomes = [
            {"source": "github.com/octo/tools", "ok": True, **INDEXED_REPOSITORY},
            {"source": "github.com/octo/docs", "ok": True, "unchanged": True, "key": "github.com/octo/docs"},
            {"source": "github.com/octo/gone", "ok": False, "error": {"code": "x", "message": "not found"}},
        ]
        self.assertEqual(
            IndexingOutcomesSummaryView(outcomes, lists_each_outcome=True).lines(UNCOLORED)[1:],
            [
                "✓ github.com/octo/docs is up to date",
                "✗ github.com/octo/gone: not found",
                "",
                "3 projects: 1 indexed, 1 up to date, 1 failed",
            ],
        )
        self.assertEqual(
            IndexingOutcomesSummaryView(outcomes, lists_each_outcome=False).lines(UNCOLORED),
            ["3 projects: 1 indexed, 1 up to date, 1 failed"],
        )


class ProjectTable(unittest.TestCase):
    def test_marks_tracked_projects_and_shortens_revisions(self):
        projects = [
            {"key": "github.com/octo/tools", "tracked": True, "ready": False, "index_directory": None},
            {
                "key": "bitbucket.org/acme/api",
                "tracked": False,
                "ready": True,
                "index_directory": "/indexes/1",
                "ref": "main",
                "commit": "7bdef4528b026ba144721106ca1bba926bec1669",
            },
        ]
        self.assertEqual(
            ProjectTableView(projects).lines(UNCOLORED),
            [
                "   PROJECT                 STATE        INDEXED  REVISION",
                "●  github.com/octo/tools   not indexed",
                "○  bitbucket.org/acme/api  ready                 main@7bdef4528b02",
                "",
                "1 tracked project (●), 1 only indexed (○)",
            ],
        )


class Tracking(unittest.TestCase):
    def test_untracking_says_what_was_undone(self):
        self.assertEqual(
            UntrackedProjectView("github.com/octo/tools", True, "/indexes/1").lines(UNCOLORED),
            ["✓ github.com/octo/tools: stopped tracking it and deleted its index"],
        )
