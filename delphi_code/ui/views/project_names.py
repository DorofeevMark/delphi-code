from ..text_layout import home_abbreviated

LOCAL_KEY_PREFIX = "local:"


def project_display_name(key: str) -> str:
    if key.startswith(LOCAL_KEY_PREFIX):
        return home_abbreviated(key.removeprefix(LOCAL_KEY_PREFIX)) or key
    return key


def project_directory_unless_named_by_it(key: str, project_directory: str | None) -> str | None:
    is_named_by_directory = key.startswith(LOCAL_KEY_PREFIX)
    return None if is_named_by_directory else home_abbreviated(project_directory)
