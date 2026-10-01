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
    return parsed(PACKAGE.glob("ui/**/*.py"))


def package_module_imported(path, relative_import):
    level = len(relative_import) - len(relative_import.lstrip("."))
    importing_package = path.parent.relative_to(PACKAGE).parts
    base_package = importing_package[: len(importing_package) - (level - 1)]
    return ".".join([*base_package, relative_import.lstrip(".")]).strip(".")


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
    def test_domain_never_imports_the_ui(self):
        for path, tree in below_wiring():
            if path.name != "controllers.py":
                for name in imported_modules(tree):
                    with self.subTest(module=path.name, imports=name):
                        self.assertNotIn(first_part(name), UI_LIBRARIES | {"ui", "cli"})

    def test_controllers_build_views_without_terminal_libraries_or_wiring(self):
        controllers = PACKAGE / "controllers.py"
        for name in imported_modules(ast.parse(controllers.read_text())):
            with self.subTest(imports=name):
                self.assertNotIn(first_part(name), UI_LIBRARIES | {"cli"})
                if first_part(name) == "ui":
                    self.assertTrue(name.lstrip(".").startswith(("ui.views", "ui.text_layout")), name)

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
                if not name.startswith("."):
                    continue
                imported = package_module_imported(path, name)
                if first_part(imported) != "ui":
                    with self.subTest(module=str(path.relative_to(PACKAGE)), imports=name):
                        self.assertIn(first_part(imported), PORTS_AND_FOUNDATIONS_FOR_UI)


if __name__ == "__main__":
    unittest.main()
