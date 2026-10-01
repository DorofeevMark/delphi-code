import ast
from pathlib import Path
import unittest

PACKAGE = Path(__file__).resolve().parent.parent / "delphi_code"
WIRING = {"cli.py", "__main__.py"}
UI_LIBRARIES = {"questionary", "prompt_toolkit", "rich", "tqdm"}
TERMINAL_STREAMS = {"stdout", "stderr"}
PORTS_AND_FOUNDATIONS_FOR_UI = {"errors", "progress"}


def parsed(paths):
    return [(path, ast.parse(path.read_text())) for path in paths]


def below_wiring():
    return parsed(path for path in PACKAGE.glob("*.py") if path.name not in WIRING)


def user_interface():
    return parsed(PACKAGE.glob("ui/*.py"))


def imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield "." * node.level + module
            yield from ("." * node.level + f"{module}.{alias.name}".lstrip(".") for alias in node.names)


def first_part(name):
    return name.lstrip(".").split(".")[0]


def terminal_uses(tree):
    return [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and (
            node.attr == "isatty"
            or (isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr in TERMINAL_STREAMS)
        )
    ]


class Layers(unittest.TestCase):
    def test_controllers_and_domain_never_import_the_ui(self):
        for path, tree in below_wiring():
            for name in imported_modules(tree):
                with self.subTest(module=path.name, imports=name):
                    self.assertNotIn(first_part(name), UI_LIBRARIES | {"ui", "cli"})

    def test_controllers_and_domain_never_touch_the_terminal(self):
        for path, tree in below_wiring():
            with self.subTest(module=path.name):
                self.assertEqual(terminal_uses(tree), [])

    def test_domain_never_imports_controllers(self):
        for path, tree in below_wiring():
            if path.name != "controllers.py":
                with self.subTest(module=path.name):
                    self.assertNotIn("controllers", map(first_part, imported_modules(tree)))

    def test_ui_knows_only_ports_and_foundations(self):
        for path, tree in user_interface():
            for name in imported_modules(tree):
                if name.startswith(".."):
                    with self.subTest(module=f"ui/{path.name}", imports=name):
                        self.assertIn(first_part(name), PORTS_AND_FOUNDATIONS_FOR_UI)


if __name__ == "__main__":
    unittest.main()
