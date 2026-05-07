"""FactCards Markdown 解析與 Gemini CLI 產檔工具。"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FACT_CARDS_DIR = PROJECT_ROOT / "runtime" / "YouTubeBridge" / "FactCards"
FACT_CARD_SOURCE_TYPE = "factcards_folder"
FACT_CARD_DEFAULT_TAGS = ["factcards", "動畫新番", "anime_new_release"]


def _load_local_prompt_template(key: str) -> str:
    prompts_path = PROJECT_ROOT / "prompts_default.json"
    try:
        data = json.loads(prompts_path.read_text(encoding="utf-8"))
        entry = data.get(key)
        return str(entry.get("template") or "") if isinstance(entry, dict) else ""
    except Exception:
        return ""


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


def build_gemini_fact_card_prompt(
    *,
    topic: str,
    output_name: str,
    session_title: str = "",
    director_guidance: str = "",
    client=None,
) -> str:
    clean_topic = str(topic or "").strip() or "動畫新番最新一話細節討論"
    clean_output = Path(output_name).name
    key = "youtube_live_fact_card_gemini_file_prompt"
    template = client.get_prompt_template(key) if client else _load_local_prompt_template(key)
    return template.format(
        output_name=clean_output,
        session_title=session_title or "動畫新番直播",
        director_guidance=director_guidance or "固定討論動畫新番，不切換到 LLM、美食或其他領域。",
        topic=clean_topic,
    )


def build_gemini_fact_card_stdout_prompt(
    *,
    topic: str,
    session_title: str = "",
    director_guidance: str = "",
    client=None,
) -> str:
    clean_topic = str(topic or "").strip() or "動畫新番最新一話細節討論"
    key = "youtube_live_fact_card_gemini_stdout_prompt"
    template = client.get_prompt_template(key) if client else _load_local_prompt_template(key)
    return template.format(
        session_title=session_title or "動畫新番直播",
        director_guidance=director_guidance or "固定討論動畫新番，不切換到 LLM、美食或其他領域。",
        topic=clean_topic,
    )


def build_local_fact_card_template(topic: str) -> str:
    """Gemini CLI 沒有產出 Markdown 時的最後保底資料卡。"""
    clean_topic = re.sub(r"\s+", " ", str(topic or "動畫新番最新話補充資料").strip())
    clean_topic = clean_topic[:180] or "動畫新番最新話補充資料"
    title_match = re.search(r"《([^》]{1,80})》", clean_topic)
    focus_title = f"《{title_match.group(1).strip()}》" if title_match else clean_topic[:40]
    return f"""# {focus_title} 自動補充資料卡

## Summary
Gemini CLI 未產生可解析 Markdown；本卡是系統依據目前直播主題建立的待驗證補充資料。
內容固定用動畫新番討論格式整理，適合先提供角色延伸方向，後續仍應以人工或下一輪 Gemini 結果替換。
本次補卡主題為「{clean_topic}」。

## Facts
### {focus_title} 的最新話具體場景
- 劇情細節：聚焦最新一話或近期集數中最容易被觀眾截圖討論的場面，例如角色做出明確選擇、戰鬥或表演段落突然轉折、或關鍵設定被揭露。
- 製作或演出細節：觀察鏡頭是否用快速切換、長鏡頭、強烈光影或重複特寫放大情緒，並留意遠景人物與動作張數是否出現落差。
- 社群討論角度：可把討論分成「演出取捨」與「排程壓力」兩派，避免只用作畫好壞一刀切。
- 可展開觀點：同一段如果只看截圖可能像崩壞，但放回動態演出是否仍有效，這點適合讓角色互相辯論。

### {focus_title} 的作畫爭議與觀看落差
- 劇情細節：挑出情緒最高的橋段，討論角色表情、手部動作或背景細節是否支撐了劇情張力。
- 製作或演出細節：可以比較近景修正精度、遠景簡化、3D 輔助與手繪修正之間的落差。
- 社群討論角度：社群常把單張截圖當證據，但動畫本身是時間媒介；角色可以討論「截圖炎上」是否公平。
- 可展開觀點：作畫不穩時，聲優、音樂與剪輯是否能補回觀看體驗，是很適合延伸的角度。

### {focus_title} 的劇情超展開
- 劇情細節：可把突然揭露、反轉、角色背叛或新敵人登場拆成「前面是否有鋪陳」與「當下衝擊是否成立」兩層。
- 製作或演出細節：超展開通常會搭配音樂停頓、畫面留白、色調切換或分鏡節奏變慢，這些都能成為討論點。
- 社群討論角度：支持者會覺得刺激，反對者可能覺得硬轉；兩位角色可以分別站在情緒派與結構派分析。
- 可展開觀點：如果超展開是為了推進季中高潮，角色可以討論它是否犧牲了角色行為邏輯。

### {focus_title} 的角色觀點衝突
- 劇情細節：整理主角、對手或配角在最新話中的目標差異，尤其是誰在保護日常、誰在推動危險選擇。
- 製作或演出細節：對話場景可觀察站位、視線方向、背景噪音與切鏡順序，這些會暗示角色關係。
- 社群討論角度：觀眾常會把角色行為分成「合理但不討喜」與「不合理但很有戲」，兩者不必混為一談。
- 可展開觀點：角色可以從不同立場替劇中人物辯護，再自然轉向下一個情節看點。

### {focus_title} 的下一集可期待焦點
- 劇情細節：根據目前已揭露的衝突，下一集通常會補上事件後果、角色修復關係或新一輪行動目標。
- 製作或演出細節：如果本集把資源集中在高潮段，下一集可能轉向文戲修正、節奏整理或世界觀補充。
- 社群討論角度：可以討論觀眾期待的是答案、名場面，還是角色互動本身。
- 可展開觀點：把「下一集會不會回收伏筆」當成角色間推理，而不是丟回觀眾等待回答。
"""


def safe_fact_card_file_name(topic: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", str(topic or "anime-fact-card")).strip("-")
    slug = slug[:60] or "anime-fact-card"
    return f"{timestamp}-{slug}.md"


def generate_fact_card_markdown_with_gemini(
    *,
    topic: str,
    output_dir: str | Path | None = None,
    output_name: str | None = None,
    session_title: str = "",
    director_guidance: str = "",
    executable: str = "gemini",
    timeout_seconds: int = 300,
    memoria_client=None,
) -> dict[str, Any]:
    root = Path(output_dir or DEFAULT_FACT_CARDS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    file_name = Path(output_name or safe_fact_card_file_name(topic)).name
    output_path = root / file_name
    prompt = build_gemini_fact_card_prompt(
        topic=topic,
        output_name=file_name,
        session_title=session_title,
        director_guidance=director_guidance,
        client=memoria_client,
    )
    executable_path = _resolve_gemini_executable(executable)
    timeout = max(30, min(int(timeout_seconds or 300), 900))
    fallback_mode = ""
    completed = _run_fact_card_markdown_stdout(
        executable_path=executable_path,
        root=root,
        topic=topic,
        session_title=session_title,
        director_guidance=director_guidance,
        timeout=timeout,
        client=memoria_client,
    )
    stdout_markdown = _valid_stdout_markdown(completed.stdout, source_name=file_name) if completed.returncode == 0 else ""
    if stdout_markdown:
        output_path.write_text(stdout_markdown, encoding="utf-8")
        fallback_mode = "stdout"
    else:
        if _direct_file_fallback_enabled():
            before_files = _snapshot_markdown_files(root)
            started_at = time.time()
            completed = _run_fact_card_direct_file(
                executable_path=executable_path,
                root=root,
                prompt=prompt,
                timeout=timeout,
            )
            if completed.returncode != 0:
                output_path.write_text(build_local_fact_card_template(topic), encoding="utf-8")
                fallback_mode = "local_template"
            if not output_path.exists():
                recovered_path = _recover_gemini_fact_card_file(
                    root=root,
                    output_path=output_path,
                    before_files=before_files,
                    started_at=started_at,
                    stdout=completed.stdout,
                )
                if recovered_path and _valid_stdout_markdown(completed.stdout, source_name=file_name):
                    fallback_mode = "stdout"
                if not recovered_path or not output_path.exists():
                    output_path.write_text(build_local_fact_card_template(topic), encoding="utf-8")
                    fallback_mode = "local_template"
        else:
            output_path.write_text(build_local_fact_card_template(topic), encoding="utf-8")
            fallback_mode = "local_template"
    markdown = output_path.read_text(encoding="utf-8")
    try:
        document = parse_fact_card_markdown(markdown, source_name=file_name)
    except ValueError:
        stdout_markdown = _generate_fact_card_markdown_stdout(
            executable_path=executable_path,
            root=root,
            topic=topic,
            session_title=session_title,
            director_guidance=director_guidance,
            timeout=timeout,
        )
        if stdout_markdown:
            output_path.write_text(stdout_markdown, encoding="utf-8")
            fallback_mode = "stdout"
        else:
            output_path.write_text(build_local_fact_card_template(topic), encoding="utf-8")
            fallback_mode = "local_template"
        markdown = output_path.read_text(encoding="utf-8")
        document = parse_fact_card_markdown(markdown, source_name=file_name)
    return {
        "path": output_path,
        "file_name": file_name,
        "document": document,
        "fallback_mode": fallback_mode,
        "returncode": completed.returncode,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def _run_fact_card_direct_file(
    *,
    executable_path: str,
    root: Path,
    prompt: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    command = [
        executable_path,
        "--skip-trust",
        "--include-directories",
        str(root),
        "--approval-mode",
        "auto_edit",
        "--prompt",
        "",
    ]
    return subprocess.run(
        command,
        cwd=root,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def _run_fact_card_markdown_stdout(
    *,
    executable_path: str,
    root: Path,
    topic: str,
    session_title: str,
    director_guidance: str,
    timeout: int,
    client=None,
) -> subprocess.CompletedProcess[str]:
    prompt = build_gemini_fact_card_stdout_prompt(
        topic=topic,
        session_title=session_title,
        director_guidance=director_guidance,
        client=client,
    )
    command = [
        executable_path,
        "--skip-trust",
        "--approval-mode",
        "plan",
        "--prompt",
        "",
    ]
    return subprocess.run(
        command,
        cwd=root,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def _direct_file_fallback_enabled() -> bool:
    value = os.environ.get("YOUTUBE_BRIDGE_GEMINI_DIRECT_FILE_FALLBACK", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _generate_fact_card_markdown_stdout(
    *,
    executable_path: str,
    root: Path,
    topic: str,
    session_title: str,
    director_guidance: str,
    timeout: int,
) -> str:
    completed = _run_fact_card_markdown_stdout(
        executable_path=executable_path,
        root=root,
        topic=topic,
        session_title=session_title,
        director_guidance=director_guidance,
        timeout=timeout,
    )
    if completed.returncode != 0:
        return ""
    return _valid_stdout_markdown(completed.stdout, source_name="gemini-stdout.md")


def _valid_stdout_markdown(stdout: str, *, source_name: str) -> str:
    markdown = _extract_stdout_markdown(stdout)
    if not markdown:
        return ""
    try:
        parse_fact_card_markdown(markdown, source_name=source_name)
    except ValueError:
        return ""
    return markdown


def _snapshot_markdown_files(root: Path) -> dict[Path, tuple[float, int]]:
    snapshot: dict[Path, tuple[float, int]] = {}
    if not root.exists():
        return snapshot
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path.resolve()] = (stat.st_mtime, stat.st_size)
    return snapshot


def _recover_gemini_fact_card_file(
    *,
    root: Path,
    output_path: Path,
    before_files: dict[Path, tuple[float, int]],
    started_at: float,
    stdout: str,
) -> Path | None:
    candidates: list[tuple[int, float, Path, str]] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            stat = path.stat()
        except OSError:
            continue
        before = before_files.get(resolved)
        changed = before is None or before != (stat.st_mtime, stat.st_size)
        if not changed and stat.st_mtime < started_at - 2:
            continue
        try:
            markdown = path.read_text(encoding="utf-8")
            parse_fact_card_markdown(markdown, source_name=path.name)
        except Exception:
            continue
        depth = len(path.relative_to(root).parents)
        score = 100 if path.parent.resolve() == root.resolve() else 0
        score -= depth
        candidates.append((score, stat.st_mtime, path, markdown))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, _, selected_path, markdown = candidates[0]
        output_path.write_text(markdown, encoding="utf-8")
        for _, _, path, _ in candidates:
            if path.resolve() == output_path.resolve():
                continue
            try:
                path.unlink()
            except OSError:
                pass
        return output_path

    markdown = _extract_stdout_markdown(stdout)
    if markdown:
        parse_fact_card_markdown(markdown, source_name=output_path.name)
        output_path.write_text(markdown, encoding="utf-8")
        return output_path
    return None


def _extract_stdout_markdown(stdout: str) -> str:
    text = _normalize_newlines(stdout)
    if not text:
        return ""
    start = text.find("# ")
    if start < 0:
        return ""
    markdown = text[start:].strip()
    if "## Summary" not in markdown or "## Facts" not in markdown:
        return ""
    return _strip_cli_stdout_diagnostics(markdown)


def _strip_cli_stdout_diagnostics(markdown: str) -> str:
    lines = _normalize_newlines(markdown).splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Warning: ") or stripped.startswith("Ripgrep is not available."):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _resolve_gemini_executable(executable: str) -> str:
    configured = os.environ.get("GEMINI_CLI_PATH", "").strip()
    candidates = [configured, executable]
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.extend([
            str(Path(appdata) / "npm" / "gemini.cmd"),
            str(Path(appdata) / "npm" / "gemini"),
        ])
    candidates.extend([
        str(Path.home() / "AppData" / "Roaming" / "npm" / "gemini.cmd"),
        str(Path.home() / "AppData" / "Roaming" / "npm" / "gemini"),
    ])
    seen: set[str] = set()
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate)
        if path.exists():
            return str(path)
    return executable


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


def _tail(text: str, *, limit: int = 1200) -> str:
    clean = str(text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def _command_error(message: str, completed: subprocess.CompletedProcess[str]) -> str:
    return (
        f"{message}；returncode={completed.returncode}；"
        f"stdout={_tail(completed.stdout)!r}；stderr={_tail(completed.stderr)!r}"
    )
