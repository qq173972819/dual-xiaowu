import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    category: str
    permissions: list[str]
    requires: dict[str, Any]
    optional: list[str]
    templates: list[str]
    maintainer: str
    risk: str
    disclaimer: str
    path: Path

    def supports(self, capabilities: dict[str, Any]) -> tuple[bool, list[str]]:
        missing: list[str] = []
        model = self.requires.get("model")
        if model and capabilities.get("model") != model:
            missing.append(f"model:{model}")
        screens = self.requires.get("screens")
        if screens and len(capabilities.get("screens", [])) < int(screens):
            missing.append(f"screens:{screens}")
        available_buttons = set(capabilities.get("buttons", []))
        for button in self.requires.get("buttons", []):
            if button not in available_buttons:
                missing.append(f"button:{button}")
        return not missing, missing

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.plugin_id, "name": self.name, "version": self.version,
            "category": self.category, "permissions": self.permissions,
            "requires": self.requires, "optional": self.optional,
            "templates": self.templates, "maintainer": self.maintainer,
            "risk": self.risk, "disclaimer": self.disclaimer,
        }


class PluginRegistry:
    def __init__(self, root: Path):
        self.root = root

    def load(self) -> list[PluginManifest]:
        manifests: list[PluginManifest] = []
        if not self.root.exists():
            return manifests
        for path in sorted(self.root.glob("*/plugin.yaml")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                manifests.append(self._validate(data, path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid plugin manifest {path}: {exc}") from exc
        return manifests

    @staticmethod
    def _validate(data: dict[str, Any], path: Path) -> PluginManifest:
        required = ["id", "name", "version", "api_version", "category", "permissions", "requires", "templates"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        if data["api_version"] != "v1":
            raise ValueError("unsupported api_version")
        return PluginManifest(
            plugin_id=str(data["id"]), name=str(data["name"]), version=str(data["version"]),
            category=str(data["category"]), permissions=list(data["permissions"]),
            requires=dict(data["requires"]), optional=list(data.get("optional", [])),
            templates=list(data["templates"]), maintainer=str(data.get("maintainer", "community")),
            risk=str(data.get("risk", "normal")), disclaimer=str(data.get("disclaimer", "")), path=path,
        )
