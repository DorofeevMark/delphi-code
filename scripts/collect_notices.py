import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import shutil

root = Path("build/third_party")
root.mkdir(exist_ok=True)
records = []
for distribution in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
    name = distribution.metadata["Name"]
    if name == "delphi-code":
        continue
    record = {
        "name": name,
        "version": distribution.version,
        "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
        "project_urls": distribution.metadata.get_all("Project-URL") or [],
        "files": {},
    }
    for file in distribution.files or []:
        if any(token in file.name.upper() for token in ("LICENSE", "NOTICE", "COPYING", "COPYRIGHT")):
            source = Path(distribution.locate_file(file))
            if source.is_file() and ".." not in file.parts:
                target = root / name / str(file)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                record["files"][target.relative_to(root).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    records.append(record)
(root / "inventory.json").write_text(json.dumps(records, indent=2) + "\n")
print(f"Preserved notices for {len(records)} installed distributions")
