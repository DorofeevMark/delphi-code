import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from huggingface_hub import HfApi, snapshot_download

parser = argparse.ArgumentParser(description="Online preparation only; never called by the CLI")
parser.add_argument("destination", type=Path)
parser.add_argument("--revision", default="1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
args = parser.parse_args()
repo = "sentence-transformers/all-MiniLM-L6-v2"
info = HfApi().model_info(repo, revision=args.revision)
revision = info.sha
if info.card_data.get("license") != "apache-2.0":
    raise SystemExit("Unexpected model license; review it before provisioning")
snapshot_download(
    repo,
    revision=revision,
    local_dir=args.destination,
    allow_patterns=["*.json", "*.txt", "*.safetensors", "1_Pooling/*", "LICENSE", "README.md"],
    ignore_patterns=["onnx/*", "openvino/*"],
)
if not (args.destination / "LICENSE").exists():
    with urlopen("https://www.apache.org/licenses/LICENSE-2.0.txt") as response:
        (args.destination / "LICENSE").write_bytes(response.read())
files = {}
for path in sorted(args.destination.rglob("*")):
    if path.is_file() and ".cache" not in path.parts:
        files[path.relative_to(args.destination).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(args.destination / "provenance.json").write_text(
    json.dumps(
        {
            "repository": repo,
            "revision": revision,
            "license": "Apache-2.0",
            "sha256": files,
        },
        indent=2,
    )
    + "\n"
)
print(args.destination.resolve())
