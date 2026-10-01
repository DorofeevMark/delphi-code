DESCRIPTION_WIDTH = 60
LARGEST_PAGE = 20
SMALLEST_PAGE = 5
LINES_AROUND_PAGE = 5
HELP = "space toggle · ctrl-a toggle matches · ctrl-t selected only · pgup/pgdn page · enter done · esc cancel"


class RepositoryList:
    def __init__(self, repositories, chosen_keys, page_size=LARGEST_PAGE):
        self._repositories = list(repositories)
        self.chosen_keys = set(chosen_keys)
        self.page_size = page_size
        self.query = ""
        self.only_chosen = False
        self.cursor = 0
        self.matches = self._matching()

    def type(self, text):
        self._change_query(self.query + text)

    def erase(self):
        self._change_query(self.query[:-1])

    def clear(self):
        self._change_query("")

    def toggle_only_chosen(self):
        self.only_chosen = not self.only_chosen
        self._rematch()

    def move(self, rows):
        self.cursor = max(0, min(len(self.matches) - 1, self.cursor + rows))

    def turn_page(self, pages):
        self.move(pages * self.page_size)

    def go_to_first(self):
        self.cursor = 0

    def go_to_last(self):
        self.move(len(self.matches))

    def toggle_current(self):
        if self.matches:
            self.chosen_keys ^= {self.matches[self.cursor].key}

    def toggle_all_matches(self):
        matched_keys = {repository.key for repository in self.matches}
        if matched_keys <= self.chosen_keys:
            self.chosen_keys -= matched_keys
        else:
            self.chosen_keys |= matched_keys

    @property
    def page(self):
        return self.cursor // self.page_size

    @property
    def page_count(self):
        return max(1, -(-len(self.matches) // self.page_size))

    def visible(self):
        start = self.page * self.page_size
        return [
            (repository, repository.key in self.chosen_keys, start + offset == self.cursor)
            for offset, repository in enumerate(self.matches[start : start + self.page_size])
        ]

    def _change_query(self, query):
        self.query = query
        self._rematch()

    def _rematch(self):
        self.matches = self._matching()
        self.cursor = 0

    def _matching(self):
        needle = self.query.casefold()
        candidates = [
            repository
            for repository in self._repositories
            if not self.only_chosen or repository.key in self.chosen_keys
        ]
        by_slug = [repository for repository in candidates if needle in repository.slug.casefold()]
        by_description_only = [
            repository
            for repository in candidates
            if needle not in repository.slug.casefold() and needle in repository.description.casefold()
        ]
        return by_slug + by_description_only


def ask_repositories(title, repositories, chosen_keys, terminal):
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import FormattedTextControl, Layout, Window

    listing = RepositoryList(repositories, chosen_keys)
    bindings = KeyBindings()

    def render():
        listing.page_size = _page_size_for(application.output.get_size().rows)
        return _fragments(title, listing)

    @bindings.add(Keys.Any)
    def type_into_filter(event):
        if event.data.isprintable():
            listing.type(event.data)

    bindings.add(Keys.BracketedPaste)(lambda event: listing.type("".join(event.data.split())))
    bindings.add("backspace")(lambda event: listing.erase())
    bindings.add("c-u")(lambda event: listing.clear())
    bindings.add(" ")(lambda event: listing.toggle_current())
    bindings.add("c-a")(lambda event: listing.toggle_all_matches())
    bindings.add("c-t")(lambda event: listing.toggle_only_chosen())
    bindings.add("up")(lambda event: listing.move(-1))
    bindings.add("down")(lambda event: listing.move(1))
    bindings.add("pageup")(lambda event: listing.turn_page(-1))
    bindings.add("pagedown")(lambda event: listing.turn_page(1))
    bindings.add("home")(lambda event: listing.go_to_first())
    bindings.add("end")(lambda event: listing.go_to_last())
    bindings.add("enter")(lambda event: event.app.exit(result=set(listing.chosen_keys)))
    bindings.add("escape", eager=True)(lambda event: event.app.exit(result=None))
    bindings.add("c-c")(lambda event: event.app.exit(result=None))

    application = Application(
        layout=Layout(Window(FormattedTextControl(render, show_cursor=False))),
        key_bindings=bindings,
        erase_when_done=True,
        input=terminal.get("input"),
        output=terminal.get("output"),
    )
    return application.run()


def repository_title(repository):
    description = repository.description.splitlines()[0] if repository.description else ""
    if len(description) > DESCRIPTION_WIDTH:
        description = description[: DESCRIPTION_WIDTH - 1] + "…"
    return f"{repository.slug}{' (private)' if repository.private else ''}{'  ' + description if description else ''}"


def _page_size_for(terminal_rows):
    return max(SMALLEST_PAGE, min(LARGEST_PAGE, terminal_rows - LINES_AROUND_PAGE))


def _fragments(title, listing):
    fragments = [
        ("class:question", f"? {title}\n"),
        ("", "  Filter: "),
        ("bold", listing.query),
        ("reverse", " "),
        ("", "\n"),
    ]
    rows = listing.visible()
    if not rows:
        fragments.append(("italic", "  No matching repositories\n"))
    for repository, chosen, current in rows:
        pointer = ">" if current else " "
        mark = "●" if chosen else "○"
        fragments.append(("reverse" if current else "", f"  {pointer} {mark} {repository_title(repository)}\n"))
    fragments.append(("", "\n" * (listing.page_size - max(1, len(rows)))))
    shown = "selected" if listing.only_chosen else "match"
    fragments.append(
        (
            "class:instruction",
            f"  Page {listing.page + 1}/{listing.page_count} · {len(listing.matches)} {shown} · "
            f"{len(listing.chosen_keys)} selected\n  {HELP}",
        )
    )
    return fragments
