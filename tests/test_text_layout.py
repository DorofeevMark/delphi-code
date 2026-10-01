from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest

from delphi_code.ui.text_layout import (
    LabelledFields,
    Table,
    TextStyle,
    excerpt_lines,
    home_abbreviated,
    pluralized,
    relative_time,
)

UNCOLORED = TextStyle(colors_enabled=False)


class Layout(unittest.TestCase):
    def test_table_aligns_columns_and_trims_trailing_space(self):
        table = Table("NAME", "STATE")
        table.add_row("long-name", "")
        table.add_row("a", "ready")
        self.assertEqual(table.lines(UNCOLORED), ["NAME       STATE", "long-name", "a          ready"])

    def test_labelled_fields_skip_empty_values_and_align_labels(self):
        fields = LabelledFields([("Model", "/m"), ("Ref", None), ("Network guard", "on")])
        self.assertEqual(fields.lines(UNCOLORED), ["  Model          /m", "  Network guard  on"])

    def test_excerpt_dedents_and_counts_hidden_lines(self):
        text = "\n".join(f"    line {number}" for number in range(1, 5))
        self.assertEqual(excerpt_lines(text, 2, "  ", UNCOLORED), ["  line 1", "  line 2", "  … 2 more lines"])

    def test_colors_only_when_enabled(self):
        self.assertEqual(TextStyle(colors_enabled=True).bold("x"), "\033[1mx\033[0m")
        self.assertEqual(UNCOLORED.bold("x"), "x")


class Wording(unittest.TestCase):
    def test_pluralizes(self):
        self.assertEqual([pluralized(1, "file"), pluralized(2, "file")], ["1 file", "2 files"])
        self.assertEqual(pluralized(3, "index", "indexes"), "3 indexes")

    def test_abbreviates_the_home_directory(self):
        self.assertEqual(home_abbreviated(str(Path.home() / "src")), "~/src")
        self.assertEqual(home_abbreviated("/opt/src"), "/opt/src")

    def test_relative_time(self):
        hours_ago = (datetime.now(UTC) - timedelta(hours=3, minutes=5)).isoformat()
        self.assertEqual(relative_time(hours_ago), "3 hours ago")
        self.assertEqual(relative_time("not a time"), "not a time")
