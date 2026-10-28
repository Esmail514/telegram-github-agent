"""User-editable CSV prompt templates for local scheduled tasks."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    name: str
    prompt: str


_FIELDNAMES = ("id", "name", "prompt")
_DEFAULTS = (
    PromptTemplate("fix-tests", "إصلاح الاختبارات", "افحص الاختبارات الفاشلة، أصلح السبب، ثم شغّل الاختبارات المناسبة."),
    PromptTemplate("code-review", "مراجعة الكود", "راجع التغييرات الحالية، حدد المشاكل المهمة، وأصلح ما يلزم مع تشغيل الاختبارات."),
    PromptTemplate("small-feature", "ميزة صغيرة", "نفذ الميزة المطلوبة بأقل تغيير ممكن، ثم شغّل الاختبارات المناسبة."),
)


class PromptTemplateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @classmethod
    def from_settings(cls) -> PromptTemplateStore:
        from app.config.settings import settings

        return cls(settings.prompt_templates_path)

    def list_all(self) -> list[PromptTemplate]:
        if not self.path.exists():
            self._write(list(_DEFAULTS))
        try:
            with self.path.open("r", encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                if reader.fieldnames is None or not set(_FIELDNAMES).issubset(reader.fieldnames):
                    raise ValueError("Prompt template CSV has missing required columns.")
                return [
                    PromptTemplate(
                        id=(row.get("id") or "").strip(),
                        name=(row.get("name") or "").strip(),
                        prompt=(row.get("prompt") or "").strip(),
                    )
                    for row in reader
                    if (row.get("id") or "").strip() and (row.get("prompt") or "").strip()
                ]
        except (OSError, csv.Error) as exc:
            raise ValueError("Could not read the prompt templates CSV.") from exc

    def get(self, template_id: str) -> PromptTemplate | None:
        return next((template for template in self.list_all() if template.id == template_id), None)

    def _write(self, templates: list[PromptTemplate]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=_FIELDNAMES)
            writer.writeheader()
            writer.writerows(
                {"id": template.id, "name": template.name, "prompt": template.prompt}
                for template in templates
            )
        os.replace(temporary, self.path)
