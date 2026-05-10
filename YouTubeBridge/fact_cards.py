"""FactCards Markdown 解析與匯入工具。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FACT_CARDS_DIR = PROJECT_ROOT / "runtime" / "YouTubeBridge" / "FactCards"
FACT_CARD_SOURCE_TYPE = "factcards_folder"
FACT_CARD_DEFAULT_TAGS = ["factcards", "動畫新番", "anime_new_release"]


@dataclass(frozen=True)
class FactCardFact:
    title: str
    body: str


@dataclass(frozen=True)
class FactCardDocument:
    title: str
    summary: str
    facts: list[FactCardFact]
    source_name: str = ""

    def to_topic_pack_entries(self, *, extra_tags: list[str] | None = None) -> list[dict[str, Any]]:
        source_tag = _source_tag(self.source_name)
        tags = [*FACT_CARD_DEFAULT_TAGS]
        if source_tag:
            tags.append(source_tag)
        for tag in extra_tags or []:
            clean_tag = str(tag or "").strip()
            if clean_tag and clean_tag not in tags:
                tags.append(clean_tag)
        entries: list[dict[str, Any]] = []
        for fact in self.facts:
            body = fact.body.strip()
            entries.append({
                "title": fact.title[:200],
                "body": body[:4000],
                "source_url": "",
                "source_type": FACT_CARD_SOURCE_TYPE,
                "tags": tags,
            })
        return entries


def parse_fact_card_markdown(markdown: str, *, source_name: str = "") -> FactCardDocument:
    text = _normalize_newlines(markdown)
    title = _first_heading(text, level=1) or Path(source_name).stem or "FactCard"
    level2 = _split_sections(text, level=2)
    summary = _clean_block(level2.get("summary") or level2.get("摘要") or "")
    facts_block = level2.get("facts") or level2.get("fact") or level2.get("事實") or ""
    if not summary:
        raise ValueError(f"{source_name or title} 缺少 ## Summary")
    if not facts_block.strip():
        raise ValueError(f"{source_name or title} 缺少 ## Facts")
    facts = _parse_fact_sections(facts_block)
    if not facts:
        raise ValueError(f"{source_name or title} 的 ## Facts 沒有可匯入的 ### 話題")
    return FactCardDocument(
        title=title[:200],
        summary=summary[:1200],
        facts=facts,
        source_name=source_name,
    )


def iter_fact_card_files(folder: str | Path | None = None, *, max_files: int = 50) -> list[Path]:
    root = Path(folder or DEFAULT_FACT_CARDS_DIR)
    if not root.exists():
        raise ValueError(f"FactCards 資料夾不存在：{root}")
    if not root.is_dir():
        raise ValueError(f"FactCards 路徑不是資料夾：{root}")
    limit = max(1, min(int(max_files or 50), 200))
    files = [path for path in root.glob("*.md") if path.is_file() and not path.name.startswith(".")]
    return sorted(files, key=lambda path: path.name.lower())[:limit]


def _normalize_newlines(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _first_heading(text: str, *, level: int) -> str:
    marker = "#" * level
    pattern = re.compile(rf"^{re.escape(marker)}\s+(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    return _clean_heading(match.group(1)) if match else ""


def _split_sections(text: str, *, level: int) -> dict[str, str]:
    marker = "#" * level
    heading_re = re.compile(rf"^{re.escape(marker)}\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = _heading_key(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[heading] = text[start:end].strip()
    return sections


def _parse_fact_sections(facts_block: str) -> list[FactCardFact]:
    heading_re = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(facts_block))
    facts: list[FactCardFact] = []
    for index, match in enumerate(matches):
        title = _clean_heading(match.group(1))[:200]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(facts_block)
        body = _clean_block(facts_block[start:end])
        if title and body:
            facts.append(FactCardFact(title=title, body=body[:3600]))
    return facts


def _clean_heading(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().strip("#")).strip()


def _clean_block(text: str) -> str:
    lines = [line.rstrip() for line in _normalize_newlines(text).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _heading_key(text: str) -> str:
    return _clean_heading(text).lower()


def _source_tag(source_name: str) -> str:
    stem = Path(source_name).stem if source_name else ""
    tag = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", stem).strip("_")
    return tag[:80]
