import ast
from pathlib import Path
import unittest

PACKAGE = Path(__file__).resolve().parent.parent / "delphi_code"
UI_LIBRARIES = {"questionary", "prompt_toolkit", "rich", "tqdm"}
TERMINAL_STREAMS = {"stdout", "stderr"}
PORTS_AND_FOUNDATIONS_FOR_UI = ("domain.errors", "services.progress")
VIEW_BUILDING_BLOCKS_FOR_CONTROLLERS = ("ui.views", "ui.text_layout")
LAYERS_EACH_LAYER_MAY_IMPORT = {
    "domain": {"domain"},
    "infrastructure": {"domain", "infrastructure"},
    "services": {"domain", "infrastructure", "services"},
    "controllers": {"domain", "infrastructure", "services", "controllers", "ui"},
    "ui": {"ui", "domain", "services"},
}
LAYERS_WITHOUT_TERMINAL_ACCESS = ("domain", "infrastructure", "services", "controllers")


def modules_of(layer):
    return [(path, ast.parse(path.read_text())) for path in sorted((PACKAGE / layer).rglob("*.py"))]


def package_imports(path, tree):
    importing_package = path.parent.relative_to(PACKAGE).parts
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base_package = importing_package[: len(importing_package) - (node.level - 1)]
            module = ".".join([*base_package, *(node.module or "").split(".")]).strip(".")
            yield from (f"{module}.{alias.name}".strip(".") for alias in node.names)


def third_party_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node.module.split(".")[0]


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
    def test_each_layer_imports_only_the_layers_beneath_it(self):
        for layer, allowed_layers in LAYERS_EACH_LAYER_MAY_IMPORT.items():
            for path, tree in modules_of(layer):
                for imported in package_imports(path, tree):
                    with self.subTest(module=str(path.relative_to(PACKAGE)), imports=imported):
                        self.assertIn(imported.split(".")[0], allowed_layers)

    def test_ui_knows_only_ports_and_foundations(self):
        for path, tree in modules_of("ui"):
            for imported in package_imports(path, tree):
                if not imported.startswith("ui."):
                    with self.subTest(module=str(path.relative_to(PACKAGE)), imports=imported):
                        self.assertTrue(imported.startswith(PORTS_AND_FOUNDATIONS_FOR_UI), imported)

    def test_controllers_use_only_views_from_the_ui(self):
        for path, tree in modules_of("controllers"):
            for imported in package_imports(path, tree):
                if imported.startswith("ui."):
                    with self.subTest(module=path.name, imports=imported):
                        self.assertTrue(imported.startswith(VIEW_BUILDING_BLOCKS_FOR_CONTROLLERS), imported)

    def test_only_the_ui_uses_terminal_libraries_and_streams(self):
        for layer in LAYERS_WITHOUT_TERMINAL_ACCESS:
            for path, tree in modules_of(layer):
                with self.subTest(module=str(path.relative_to(PACKAGE))):
                    self.assertFalse(UI_LIBRARIES & set(third_party_imports(tree)))
                    self.assertEqual(terminal_uses(tree), [])


if __name__ == "__main__":
    unittest.main()
