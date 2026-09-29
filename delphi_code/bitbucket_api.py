import base64
import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

PAGE_LENGTH = 100
TIMEOUT_SECONDS = 60
REPOSITORY_FIELDS = "next,values.slug,values.name,values.description,values.is_private,values.updated_on"


def api_base():
    return os.environ.get("DELPHI_CODE_BITBUCKET_API_BASE", "https://api.bitbucket.org/2.0").rstrip("/")


def workspaces_url():
    return f"{api_base()}/user/workspaces?pagelen={PAGE_LENGTH}"


def legacy_workspaces_url():
    return f"{api_base()}/user/permissions/workspaces?pagelen={PAGE_LENGTH}"


def repositories_url(workspace):
    query = urlencode({"role": "member", "pagelen": PAGE_LENGTH, "sort": "slug", "fields": REPOSITORY_FIELDS})
    return f"{api_base()}/repositories/{quote(workspace)}?{query}"


def get_json(url):
    credentials = f"{os.environ['DELPHI_CODE_BITBUCKET_API_USERNAME']}:{os.environ['DELPHI_CODE_BITBUCKET_API_PASSWORD']}"
    headers = {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(), "Accept": "application/json"}
    with urlopen(Request(url, headers=headers), timeout=TIMEOUT_SECONDS, context=verified_tls() if url.startswith("https:") else None) as response:
        return json.load(response)


def verified_tls():
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def all_pages(url):
    values = []
    while url:
        page = get_json(url)
        values.extend(page.get("values", []))
        url = page.get("next")
    return values


def workspaces():
    try:
        memberships = all_pages(workspaces_url())
    except HTTPError as exc:
        exc.close()
        if exc.code not in (404, 410):
            raise
        memberships = all_pages(legacy_workspaces_url())
    names_by_slug = {}
    for membership in memberships:
        workspace = membership.get("workspace", membership)
        names_by_slug[workspace["slug"]] = workspace.get("name") or workspace["slug"]
    return [{"slug": slug, "name": name} for slug, name in sorted(names_by_slug.items())]


def repositories(workspace):
    return [{"slug": repository["slug"], "name": repository.get("name") or repository["slug"],
             "description": repository.get("description") or "", "private": bool(repository.get("is_private")),
             "updated_on": repository.get("updated_on")}
            for repository in all_pages(repositories_url(workspace))]


def main():
    command, *arguments = sys.argv[1:]
    try:
        result = workspaces() if command == "workspaces" else repositories(*arguments)
    except HTTPError as exc:
        exc.close()
        print(json.dumps({"error": {"status": exc.code, "message": f"HTTP {exc.code} {exc.reason}"}}))
        raise SystemExit(1)
    except (URLError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": {"status": None, "message": str(exc)}}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
