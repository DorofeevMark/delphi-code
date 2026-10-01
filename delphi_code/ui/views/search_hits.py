from ..text_layout import TextStyle, excerpt_lines
from .project_names import project_display_name

EXCERPT_LINE_LIMIT = 6
EXCERPT_INDENT = "    "


class SearchHitsView:
    def __init__(self, query: str, searched_scope: str, hits: list[dict], names_project_of_each_hit: bool):
        self._query = query
        self._searched_scope = searched_scope
        self._hits = hits
        self._names_project_of_each_hit = names_project_of_each_hit

    def lines(self, style: TextStyle) -> list[str]:
        lines = [style.bold(self._heading()) + style.dim(f" in {self._searched_scope}")]
        for hit in self._hits:
            lines.append("")
            lines.extend(self._hit_lines(hit, style))
        return lines

    def _heading(self) -> str:
        count = len(self._hits)
        return f'{count or "No"} {"match" if count == 1 else "matches"} for "{self._query}"'

    def _hit_lines(self, hit: dict, style: TextStyle) -> list[str]:
        location = f"{hit['path']}:{hit['start_line']}-{hit['end_line']}"
        lines = [f"{style.cyan(location)}  {style.dim(hit['language'])}  score {hit['score']:.2f}"]
        if "url" in hit:
            lines.append(f"  {style.dim(hit['url'])}")
        elif self._names_project_of_each_hit:
            lines.append(f"  {style.dim(project_display_name(hit['key']))}")
        return lines + excerpt_lines(hit["text"], EXCERPT_LINE_LIMIT, EXCERPT_INDENT, style)
