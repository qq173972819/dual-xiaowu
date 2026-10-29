"""开发者沙箱：从 GitHub 导入纯静态工程，并按 4.2 寸双屏墨水屏规则自动校验。

设计约束（与产品约定一致）：
- MVP 只托管**纯静态**产物（HTML/CSS/JS/图片），不执行构建、不跑服务端；
- 校验是静态分析（不依赖浏览器），结论分 pass / warn / fail，只有 fail 会阻止提交审核；
- 所有产物落在 ``data/sandbox/<app_id>/repo``，由 ``/sandbox/<app_id>/...`` 直接对外托管。
"""

from __future__ import annotations

import json
import re
import shutil
import tarfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 4.2 寸双屏：FPC1 400x300 黑白，FPC2 400x300 四色（黑/白/红/黄）
SCREEN_WIDTH = 400
SCREEN_HEIGHT = 300
MAX_REPO_BYTES = 40 * 1024 * 1024
MAX_FILES = 2000
CLONE_TIMEOUT = 180
ALLOWED_HOSTS = {"github.com", "www.github.com"}
TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".svg", ".json", ".txt", ".md"}
ENTRY_CANDIDATES = (
    "index.html", "public/index.html", "src/index.html", "dist/index.html",
    "www/index.html", "site/index.html", "docs/index.html",
)


class SandboxError(Exception):
    """导入或校验失败，message 直接回显给开发者。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_repo_url(raw: str) -> tuple[str, str]:
    """校验并规范化 GitHub 仓库地址，返回 (clone_url, owner/repo)。"""
    value = (raw or "").strip()
    if not value:
        raise SandboxError("请填写 GitHub 仓库地址")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.hostname not in ALLOWED_HOSTS:
        raise SandboxError("目前只支持 github.com 上的公开仓库")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise SandboxError("仓库地址需形如 https://github.com/<owner>/<repo>")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not re.match(r"^[A-Za-z0-9._-]+$", owner) or not re.match(r"^[A-Za-z0-9._-]+$", repo):
        raise SandboxError("仓库地址格式不合法")
    return f"https://github.com/{owner}/{repo}.git", f"{owner}/{repo}"


def download_tarball(owner_repo: str, ref: str, dest: Path, timeout: int = CLONE_TIMEOUT) -> str:
    """从 GitHub 的 codeload CDN 下载仓库快照并解压，返回提交短哈希。

    不用 ``git clone``：沙箱/内网环境往往只有 HTTPS 代理可达，git 的 schannel
    后端在中间人证书下会报 "server closed abruptly"；codeload 走纯 HTTPS 更稳，
    且解压目录名自带短哈希（``repo-<sha12>``）。
    """
    safe_ref = (ref or "HEAD").strip()
    if not re.match(r"^[A-Za-z0-9._/-]+$", safe_ref):
        raise SandboxError("分支名包含非法字符")
    if safe_ref != "HEAD":
        safe_ref = "refs/heads/" + safe_ref
    url = f"https://codeload.github.com/{owner_repo}/tar.gz/{safe_ref}"

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)

    tmp_archive = dest.parent / ".download.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "xiaowu-sandbox/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_REPO_BYTES:
                raise SandboxError(f"仓库压缩包 {length / 1048576:.1f} MB，超过沙箱上限 {MAX_REPO_BYTES // 1048576} MB")
            written = 0
            with tmp_archive.open("wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_REPO_BYTES:
                        raise SandboxError(f"仓库压缩包超过沙箱上限 {MAX_REPO_BYTES // 1048576} MB")
                    handle.write(chunk)
    except SandboxError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except urllib.error.HTTPError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        if exc.code == 404:
            raise SandboxError(f"仓库或分支不存在：{owner_repo}（{safe_ref}）。请确认仓库是公开的、分支名正确") from exc
        raise SandboxError(f"下载仓库失败：HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise SandboxError(f"下载仓库失败：{exc}. 若服务器需代理访问外网，请配置 HTTPS_PROXY") from exc

    commit = ""
    try:
        with tarfile.open(tmp_archive, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES * 4:
                raise SandboxError("仓库文件数量过多，已拒绝导入")
            archive.extractall(dest, filter="data")  # 3.12+ 数据过滤：拒绝绝对路径/软链接逃逸
            roots = {m.name.split("/", 1)[0] for m in members if m.name}
        root_name = next(iter(roots), "")
        commit = root_name.rsplit("-", 1)[-1] if "-" in root_name else root_name
        # codeload 产物统一包一层 <repo>-<sha>/，剥掉让 index.html 落在仓库根
        inner = dest / root_name
        if len(roots) == 1 and inner.is_dir():
            for child in list(inner.iterdir()):
                shutil.move(str(child), str(dest / child.name))
            inner.rmdir()
    except SandboxError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise SandboxError(f"解压仓库失败：{exc}") from exc
    finally:
        tmp_archive.unlink(missing_ok=True)
    if not commit or commit.upper() == "HEAD":
        commit = resolve_commit(owner_repo, ref) or (ref or "HEAD")
    return commit


def resolve_commit(slug: str, ref: str = "") -> str:
    """尽力从 GitHub API 取提交短哈希；失败不影响导入（返回空串）。"""
    target = ref or "HEAD"
    url = f"https://api.github.com/repos/{slug}/commits/{target}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "xiaowu-sandbox/1.0", "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read(200_000).decode("utf-8", "ignore"))
        return str(payload.get("sha", ""))[:12]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""


def repo_stats(root: Path) -> tuple[int, int]:
    files = size = 0
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            files += 1
            size += path.stat().st_size
    return files, size


def find_entry(root: Path, hint: str = "") -> Path | None:
    if hint:
        candidate = (root / hint).resolve()
        if candidate.is_file() and str(candidate).startswith(str(root.resolve())):
            return candidate
        return None
    for rel in ENTRY_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    html_files = [p for p in root.rglob("*.htm*") if ".git" not in p.parts]
    if len(html_files) == 1:
        return html_files[0]
    if html_files:
        shallow = sorted(html_files, key=lambda p: len(p.relative_to(root).parts))
        return shallow[0]
    return None


# ---------------------------------------------------------------- 规则校验

_INLINE_STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.I | re.S)
_STYLE_ATTR = re.compile(r'style\s*=\s*["\']([^"\']*)["\']', re.I)
_LINK_CSS = re.compile(r'<link[^>]+href\s*=\s*["\']([^"\']+\.css[^"\']*)["\']', re.I)
_FONT_SIZE = re.compile(r"font-size\s*:\s*([\d.]+)\s*(px|rem|em|pt|%)", re.I)
_HEX_COLOR = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_RGB_COLOR = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", re.I)
_MIN_WIDTH = re.compile(r"min-width\s*:\s*(\d{2,4})\s*px", re.I)
_FIXED_WIDTH = re.compile(r"(?<!min-)(?<!max-)width\s*:\s*(\d{3,4})\s*px", re.I)


def _read_text(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _collect_css(root: Path, html: str) -> str:
    chunks = list(_INLINE_STYLE.findall(html)) + _STYLE_ATTR.findall(html)
    for href in _LINK_CSS.findall(html):
        if href.startswith(("http://", "https://", "//")):
            continue
        target = (root / href.lstrip("./")).resolve()
        if str(target).startswith(str(root.resolve())) and target.is_file():
            chunks.append(_read_text(target))
    return "\n".join(chunks)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _classify_color(rgb: tuple[int, int, int]) -> str:
    """把颜色归类为 e-ink 四色可用集合：neutral / red / yellow / unsupported。"""
    r, g, b = (c / 255 for c in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    if hi - lo < 0.12:
        return "neutral"
    # 色相（度）
    if hi == r:
        hue = (60 * ((g - b) / (hi - lo))) % 360
    elif hi == g:
        hue = 60 * ((b - r) / (hi - lo)) + 120
    else:
        hue = 60 * ((r - g) / (hi - lo)) + 240
    if hue < 20 or hue >= 340:
        return "red"
    if 40 <= hue <= 70:
        return "yellow"
    return "unsupported"


def validate_site(root: Path, entry: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, label: str, status: str, detail: str) -> None:
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail})

    html = _read_text(entry)
    css = _collect_css(root, html)
    source = html + "\n" + css

    # 1) 纯静态：不依赖构建 / 服务端
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = (json.loads(_read_text(pkg)) or {}).get("scripts") or {}
        except json.JSONDecodeError:
            scripts = {}
        if "build" in scripts and not (root / "dist").is_dir():
            add("static_build", "纯静态托管", "warn",
                "检测到 package.json 的 build 脚本：沙箱不执行构建，请把构建产物（如 dist/）提交到仓库，或改为纯静态页面")
        else:
            add("static_build", "纯静态托管", "pass", "检测到已构建产物，可直接托管")
    else:
        add("static_build", "纯静态托管", "pass", "纯静态工程，无需构建")

    # 2) 入口页面
    add("entry", "入口页面", "pass", f"入口：{entry.relative_to(root).as_posix()}")

    # 3) 不依赖动画
    animations = []
    for pattern, label in ((re.compile(r"@keyframes", re.I), "@keyframes 动画"),
                           (re.compile(r"[^-]animation\s*:", re.I), "animation 属性"),
                           (re.compile(r"<marquee", re.I), "<marquee> 跑马灯"),
                           (re.compile(r"<video", re.I), "<video> 视频"),
                           (re.compile(r"<canvas", re.I), "<canvas> 画布")):
        if pattern.search(source):
            animations.append(label)
    if re.search(r"<video", html, re.I):
        add("no_animation", "不依赖动画表达状态", "fail",
            "页面包含 <video>：墨水屏无法播放视频，请改为静态首帧图片")
    elif animations:
        add("no_animation", "不依赖动画表达状态", "fail",
            f"检测到 {'、'.join(animations)}：墨水屏刷新一次需数秒，动画会导致残影与硬件损耗，请改为静态表达")
    else:
        add("no_animation", "不依赖动画表达状态", "pass", "未检测到动画、视频或跑马灯")

    # 4) 字号 ≥18px
    sizes: list[float] = []
    for value, unit in _FONT_SIZE.findall(css):
        try:
            num = float(value)
        except ValueError:
            continue
        unit = unit.lower()
        px = num * 16 if unit in ("rem", "em") else num * 4 / 3 if unit == "pt" else num * 0.16 if unit == "%" else num
        if px >= 6:
            sizes.append(round(px, 1))
    if not sizes:
        add("font_size", "文字字号 ≥18px", "warn", "未显式声明字号（浏览器默认约 16px），建议显式设为 18px 以上")
    else:
        smallest = min(sizes)
        if smallest < 14:
            add("font_size", "文字字号 ≥18px", "fail",
                f"最小字号 {smallest}px：4.2 寸屏上无法辨认，请全部提升到 18px 以上")
        elif smallest < 18:
            add("font_size", "文字字号 ≥18px", "warn",
                f"最小字号 {smallest}px：低于平台建议的 18px，远距离阅读会吃力")
        else:
            add("font_size", "文字字号 ≥18px", "pass", f"最小字号 {smallest}px，满足 18px 要求")

    # 5) 宽度适配 400px（4.2 寸 FPC1/FPC2 均为 400x300）
    too_wide_min = [int(v) for v in _MIN_WIDTH.findall(css) if int(v) > SCREEN_WIDTH]
    wide_fixed = sorted({int(v) for v in _FIXED_WIDTH.findall(css) if int(v) > SCREEN_WIDTH})
    if too_wide_min:
        add("viewport_fit", "适配 4.2 寸（400×300）", "fail",
            f"CSS 声明 min-width:{max(too_wide_min)}px，超过屏幕宽度 400px，设备上会被裁切")
    elif wide_fixed and max(wide_fixed) > 480:
        add("viewport_fit", "适配 4.2 寸（400×300）", "warn",
            f"检测到固定宽度 {max(wide_fixed)}px，建议改为 max-width:100% 或流式布局")
    else:
        add("viewport_fit", "适配 4.2 寸（400×300）", "pass", "未检测到超出 400px 的硬性宽度")

    if 'name="viewport"' not in html and "name='viewport'" not in html:
        add("viewport_meta", "声明 viewport", "warn",
            "缺少 <meta name=\"viewport\">，设备端按 400×300 渲染时可能被缩放")

    # 6) 颜色落在四色墨水屏可表达范围
    colors: set[tuple[int, int, int]] = set()
    for value in _HEX_COLOR.findall(css):
        colors.add(_hex_to_rgb(value))
    for r, g, b in _RGB_COLOR.findall(css):
        colors.add((int(r), int(g), int(b)))
    unsupported = sorted({c for c in colors if _classify_color(c) == "unsupported"})
    if unsupported:
        sample = "、".join("#%02x%02x%02x" % c for c in unsupported[:6])
        add("palette", "色板限定黑/白/红/黄", "warn",
            f"检测到四色屏无法表达的色相（如 {sample}），FPC2 上会被强制量化；建议只用黑、白、红、黄与灰阶")
    elif colors:
        add("palette", "色板限定黑/白/红/黄", "pass", f"共 {len(colors)} 种颜色，均在四色可表达范围内")
    else:
        add("palette", "色板限定黑/白/红/黄", "pass", "未使用自定义颜色（默认黑白）")

    # 7) 外部依赖
    if re.search(r'<script[^>]+src\s*=\s*["\']https?://', html, re.I):
        add("self_contained", "资源自包含", "warn",
            "引用了外部 CDN 脚本：设备联网受限时会加载失败，建议把资源一起提交到仓库")

    # 8) 大面积反色（黑底）
    if re.search(r"(body|:root|html)[^{}]*\{[^}]*background(-color)?\s*:\s*(#0[0-9a-f]{2,5}|black)", css, re.I):
        add("invert_area", "避免大面积连续反色", "warn",
            "整页黑底白字：全屏反色刷新慢且易残影，建议只在小块区域反色突出")

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    return {
        "passed": not failed,
        "verdict": "fail" if failed else ("warn" if warned else "pass"),
        "checks": checks,
        "summary": {
            "pass": sum(1 for c in checks if c["status"] == "pass"),
            "warn": len(warned),
            "fail": len(failed),
        },
        "screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT, "target": "4.2 寸双屏（FPC1 黑白 / FPC2 四色）"},
        "validated_at": _now(),
    }


def import_static_site(repo_url: str, dest_root: Path, branch: str = "", entry: str = "") -> dict[str, Any]:
    """下载仓库快照 → 校验 → 落盘。失败时抛出 SandboxError。"""
    _clone_url, slug = normalize_repo_url(repo_url)
    dest_root.mkdir(parents=True, exist_ok=True)
    staging = dest_root / ".staging"
    target = dest_root / "repo"

    commit = download_tarball(slug, branch, staging)
    files, size = repo_stats(staging)
    if files > MAX_FILES or size > MAX_REPO_BYTES:
        shutil.rmtree(staging, ignore_errors=True)
        raise SandboxError(f"仓库过大（{files} 个文件 / {size / 1048576:.1f} MB），沙箱上限为 {MAX_FILES} 个文件、{MAX_REPO_BYTES // 1048576} MB")
    entry_path = find_entry(staging, entry)
    if entry_path is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise SandboxError("仓库里没有找到入口 HTML（index.html），请在仓库根目录或 public/、src/、dist/ 下放置 index.html")

    report = validate_site(staging, entry_path)

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    entry_rel = (target / entry_path.relative_to(staging)).relative_to(target).as_posix()
    return {
        "repo_url": f"https://github.com/{slug}",
        "repo_slug": slug,
        "branch": branch,
        "commit_sha": commit,
        "entry": entry_rel,
        "root": target,
        "file_count": files,
        "size_bytes": size,
        "report": report,
    }


def resolve_asset(root: Path, rel: str) -> Path | None:
    """把请求路径安全地映射到仓库内文件，拒绝目录穿越。"""
    clean = (rel or "").strip().lstrip("/")
    try:
        base = root.resolve()
        target = (base / clean).resolve()
    except (OSError, ValueError):
        return None
    if not str(target).startswith(str(base)):
        return None
    if target.is_dir():
        index = target / "index.html"
        return index if index.is_file() else None
    return target if target.is_file() else None
