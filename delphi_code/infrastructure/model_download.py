import json
import logging
from pathlib import Path
import ssl
import sys
from urllib.request import urlopen


def main():
    from huggingface_hub import snapshot_download

    # The Hub nags anonymous clients to set HF_TOKEN; the pinned public model needs none.
    logging.getLogger("huggingface_hub.utils._http").addFilter(lambda record: "HF_TOKEN" not in record.getMessage())

    manifest = json.loads(Path(__file__).with_name("model_provenance.json").read_text())
    destination = Path(sys.argv[1])
    snapshot_download(
        manifest["repository"],
        revision=manifest["revision"],
        local_dir=destination,
        allow_patterns=list(manifest["sha256"]),
    )
    if not (destination / "LICENSE").exists():
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
        with urlopen("https://www.apache.org/licenses/LICENSE-2.0.txt", timeout=60, context=context) as response:
            (destination / "LICENSE").write_bytes(response.read())


if __name__ == "__main__":
    main()
