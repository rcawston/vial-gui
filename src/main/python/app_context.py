import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RESOURCE_ROOT = REPO_ROOT / "src" / "main" / "resources" / "base"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def bundle_root():
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return REPO_ROOT


class AppContext:
    def __init__(self):
        if is_frozen():
            candidates = [
                bundle_root() / "build_settings.json",
                bundle_root() / "base.json",
            ]
            settings_path = next((path for path in candidates if path.exists()), candidates[0])
        else:
            settings_path = REPO_ROOT / "src" / "build" / "settings" / "base.json"
        with settings_path.open("r", encoding="utf-8") as inf:
            self.build_settings = json.load(inf)

    def get_resource(self, path):
        if is_frozen():
            return os.path.join(bundle_root(), "resources", path)
        return str(RESOURCE_ROOT / path)
