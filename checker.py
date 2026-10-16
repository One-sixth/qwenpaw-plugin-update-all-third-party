# -*- coding: utf-8 -*-
"""checker — 第三方插件更新检查核心。

流程：
1. 扫描本地插件目录（磁盘 plugin.json，不依赖加载状态）；
2. 拉官方 catalog，catalog 内的 plugin_id 视为官方插件 → 跳过
   （官方插件由插件商店自带的「全部更新」机制负责）；
3. 对每个第三方插件调用市场精确搜索（``search=<本地 id>``），
   请求间隔 SEARCH_INTERVAL 秒，避免一次性打满市场接口；
4. 匹配规则（用户拍板）：**市场 id 短名 == 本地插件 id**（规范化：
   去 @ 前缀、去空白、小写）。市场 id 形如 ``@owner/name``，
   短名即 ``name`` 段；
5. 版本比较（semver，Version 解析失败退化字符串比较），市场版本
   更高 → 待更新候选；候选带 author_mismatch 标记（本地 author 与
   市场 owner/developer 均不一致时）。

官方「全部更新」避开第三方插件的三道闸门（2026-10 实测结论，本模块绕开）：
  a. 官方 catalog 只含官方 CDN 条目 —— 第三方永远没有 upgrade_available；
  b. 前端身份匹配要求「本地 author ∈ 市场 {owner, developer}」，
     本地 author 是自由文本（onesixth vs onesixmix、Erick vs
     erickcharles）→ 大量失配；
  c. 前端按 updated_time 翻页拉全市场做匹配，实测第 3 页即超时，
     翻不到的插件连参与匹配的机会都没有。
本模块改为逐插件精确搜索（一次请求定位一个插件），并放弃 author
比对（匹配只看 id 短名），author 差异仅作警示不作为匹配条件。
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

MARKET_BASE_URL = "https://platform.agentscope.io"
MARKET_SEARCH_PATH = "/openapi/v1/plugins"
MARKET_ARCHIVE_TPL = (
    MARKET_BASE_URL + "/plugins/{owner}/{name}/archive/zip/master"
)
MARKET_PAGE_URL_TPL = MARKET_BASE_URL + "/plugins/{name}"

#: 相邻市场搜索请求的间隔（秒）——避免瞬时流量峰值。
SEARCH_INTERVAL = 0.2
#: 单次搜索超时（秒）。
SEARCH_TIMEOUT = 15.0
#: check 结果缓存有效期（秒）；apply 复用缓存，过期要求重新 check。
#: 24 小时（用户拍板：轻量内存方案，日级刷新节奏足够）。
REPORT_TTL = 86400.0


class BusyError(RuntimeError):
    """同类操作已在进行中（幂等保护：立即提示，不排队）。"""


# ── 数据结构 ────────────────────────────────────────────────────────────


@dataclass
class InstalledPlugin:
    """本地插件目录里的一个插件（磁盘 manifest 口径）。"""

    id: str
    name: str
    version: str
    author: str
    dir_name: str


@dataclass
class UpdateItem:
    """一个可更新的第三方插件候选。"""

    plugin_id: str
    name: str
    local_version: str
    local_author: str
    market_id: str
    market_owner: str
    market_version: str
    market_author: str
    source: str  # 市场归档 ZIP 直链（install 的 source）
    detail_url: str  # 市场插件页面（作者不一致时建议人工确认）
    author_mismatch: bool = False


@dataclass
class CheckReport:
    """check_all 的完整报告。"""

    checked_at: float = 0.0
    total_installed: int = 0
    official_count: int = 0
    third_party_count: int = 0
    updates: list[UpdateItem] = field(default_factory=list)
    latest: list[dict] = field(default_factory=list)
    not_in_market: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── 基础工具 ────────────────────────────────────────────────────────────


def normalize_id(raw: str) -> str:
    """规范化插件 id：去 @ 前缀、去首尾空白、小写。"""
    return (raw or "").strip().lstrip("@").lower()


def short_name(market_id: str) -> str:
    """市场 id（@owner/name）的短名段。"""
    n = normalize_id(market_id)
    return n[n.rfind("/") + 1 :] if n else ""


def normalize_author(raw: Optional[str]) -> str:
    """规范化作者名（比对用）：去 @ 前缀、去空白、小写。"""
    return (raw or "").strip().lstrip("@").lower()


def compare_versions(local: str, market: str) -> int:
    """比较版本：返回 >0 / 0 / <0 表示 local 相对 market 的高低。

    与 QwenPaw 官方 download_catalog._is_upgrade_available 语义对齐：
    任一方无法按 semver 解析时，**不等即视为可更新**（返回 -1，
    相等返回 0）——不再按字符串序判高低（字符串序会把 "2.0" vs
    "10.0" 这类错判成 local 更高而漏报，0.2.6 修正）。
    """
    if not local or not market:
        return 0
    try:
        return (Version(local) > Version(market)) - (
            Version(local) < Version(market)
        )
    except InvalidVersion:
        # 官方对齐：非 semver 时「相等=最新，不等=可更新」。
        return 0 if local == market else -1


def build_archive_url(market_entry: dict) -> str:
    """市场条目 → 归档 ZIP 直链（官方 console buildMarketDownloadUrl 同款）。"""
    owner = str(market_entry.get("owner") or "")
    mid = normalize_id(str(market_entry.get("id") or ""))
    name = mid[mid.rfind("/") + 1 :] if mid else ""
    return MARKET_ARCHIVE_TPL.format(owner=owner, name=name)


def build_detail_url(market_entry: dict) -> str:
    """市场条目 → 插件详情页（人工确认用）。"""
    mid = normalize_id(str(market_entry.get("id") or ""))
    name = mid[mid.rfind("/") + 1 :] if mid else ""
    return MARKET_PAGE_URL_TPL.format(name=name)


def market_author(entry: dict) -> str:
    """市场条目的展示作者（developer 优先，owner 兜底）。"""
    dev = str(entry.get("developer") or "").strip()
    return dev or str(entry.get("owner") or "").strip()


# ── 本地扫描 ────────────────────────────────────────────────────────────


def _read_installed_from_disk() -> list[InstalledPlugin]:
    """扫描本地插件目录（get_plugins_dir），返回磁盘 manifest 口径列表。"""
    from qwenpaw.config.utils import get_plugins_dir

    plugins_dir = get_plugins_dir()
    if not plugins_dir.is_dir():
        return []

    result: list[InstalledPlugin] = []
    for item in sorted(plugins_dir.iterdir()):
        if not item.is_dir():
            continue
        manifest_path = item / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("checker 跳过 %s: %s", manifest_path, exc)
            continue
        result.append(
            InstalledPlugin(
                id=str(manifest.get("id") or item.name),
                name=str(manifest.get("name") or item.name),
                version=str(manifest.get("version") or "0.0.0"),
                author=str(manifest.get("author") or ""),
                dir_name=item.name,
            )
        )
    return result


async def _fetch_official_ids() -> set[str]:
    """官方 catalog 里的 plugin_id 集合（复用宿主 download_catalog）。"""
    from qwenpaw.plugins.download_catalog import build_plugin_catalog

    catalog = await asyncio.to_thread(build_plugin_catalog)
    if catalog.get("error"):
        raise RuntimeError(
            f"官方插件目录拉取失败：{catalog.get('error')}。"
            "无法区分官方/第三方插件，已中止检查（避免把官方插件误当"
            "第三方覆盖安装）。请稍后重试。"
        )
    return {
        str(p.get("plugin_id") or "")
        for p in (catalog.get("plugins") or [])
        if p.get("plugin_id")
    }


# ── 市场搜索 ────────────────────────────────────────────────────────────


async def _search_market(term: str, page_size: int = 20) -> list[dict]:
    """市场精确搜索，返回 data.plugins 列表。

    响应结构异常（data/plugins 键缺失或类型不符）时抛 RuntimeError
    走 errors 通道——静默返回空列表会把全部插件误报成「不在市场」。
    """
    import httpx

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        resp = await client.get(
            MARKET_BASE_URL + MARKET_SEARCH_PATH,
            params={
                "page_number": 1,
                "page_size": page_size,
                "search": term,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    return parse_search_body(body)


def parse_search_body(body: Any) -> list[dict]:
    """解析市场搜索响应体；结构漂移时抛错（进 errors 而非误报）。"""
    if not isinstance(body, dict):
        raise RuntimeError(
            f"插件市场响应结构异常：顶层为 {type(body).__name__}（期望 dict）"
        )
    data = body.get("data")
    if data is None:
        # 无 data 键：兼容空响应（无结果）与结构漂移。
        if body.get("success") is False:
            raise RuntimeError(
                f"插件市场返回失败：{body.get('message') or '未知原因'}"
            )
        return []
    if not isinstance(data, dict):
        raise RuntimeError(
            f"插件市场响应结构异常：data 为 {type(data).__name__}（期望 dict）"
        )
    plugins = data.get("plugins")
    if plugins is None:
        return []
    if not isinstance(plugins, list):
        raise RuntimeError(
            f"插件市场响应结构异常：data.plugins 为 "
            f"{type(plugins).__name__}（期望 list）"
        )
    return list(plugins)


def _match_market_entries(
    local: InstalledPlugin, entries: list[dict]
) -> tuple[Optional[dict], list[dict]]:
    """按「市场 id 短名 == 本地 id」匹配市场条目。

    Returns:
        (唯一匹配, 全部同短名候选)；唯一匹配为 None 时候选可能
        为空（不在市场）或有多个（歧义）。
    """
    local_id = normalize_id(local.id)
    matched = [
        e for e in entries if short_name(str(e.get("id") or "")) == local_id
    ]
    if len(matched) == 1:
        return matched[0], []
    return None, matched


# ── 主流程 ──────────────────────────────────────────────────────────────

_LAST_REPORT: Optional[CheckReport] = None
#: 检查进行中标志（幂等保护）。原子性前提：宿主 web 端点与 async
#: 工具运行在同一事件循环（协作式调度下检查+置位之间无 await 点，
#: 无竞态窗口）；若未来出现多线程/多 loop 驱动，需改 threading.Lock。
_CHECKING = False


def invalidate_report() -> None:
    """作废检查报告缓存（apply 批次结束后调用，防已更新项被重复消费）。"""
    global _LAST_REPORT
    _LAST_REPORT = None


def last_report(max_age: float = REPORT_TTL) -> Optional[CheckReport]:
    """最近一次 check 的报告（超龄返回 None）。"""
    if _LAST_REPORT is None:
        return None
    if time.time() - _LAST_REPORT.checked_at > max_age:
        return None
    return _LAST_REPORT


def check_author_mismatch(local: InstalledPlugin, entry: dict) -> bool:
    """本地 author 与市场 owner/developer 均不一致时为 True。

    本地 author 为空时不算不一致（无从比对）。
    """
    la = normalize_author(local.author)
    if not la:
        return False
    return normalize_author(entry.get("owner")) != la and (
        normalize_author(entry.get("developer")) != la
    )


async def check_all() -> CheckReport:
    """检查全部第三方插件更新（主入口）。

    请求节奏：相邻市场搜索间隔 SEARCH_INTERVAL 秒。
    幂等保护：同一时刻只允许一个检查在跑，重复调用立即抛
    BusyError（提示等待，不排队）。
    """
    global _CHECKING

    if _CHECKING:
        raise BusyError(
            "正在检查插件更新，请等待当前检查完成后再试"
        )
    _CHECKING = True
    try:
        return await _check_all_locked()
    finally:
        _CHECKING = False


async def _check_all_locked() -> CheckReport:
    """check_all 的实际执行体（调用方已持有 busy 标志）。"""
    global _LAST_REPORT

    installed = await asyncio.to_thread(_read_installed_from_disk)
    official_ids = await _fetch_official_ids()

    third_party = [p for p in installed if p.id not in official_ids]

    report = CheckReport(
        checked_at=time.time(),
        total_installed=len(installed),
        official_count=len(installed) - len(third_party),
        third_party_count=len(third_party),
    )

    for index, local in enumerate(third_party):
        if index > 0:
            await asyncio.sleep(SEARCH_INTERVAL)
        try:
            entries = await _search_market(local.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("checker 市场搜索失败 %s: %s", local.id, exc)
            report.errors.append(
                {"plugin_id": local.id, "error": str(exc)}
            )
            continue

        entry, multi = _match_market_entries(local, entries)

        if entry is None and multi:
            # 同短名多候选：不自动选，列出供人工决定。
            report.ambiguous.append(
                {
                    "plugin_id": local.id,
                    "local_version": local.version,
                    "candidates": [
                        {
                            "market_id": str(e.get("id") or ""),
                            "owner": str(e.get("owner") or ""),
                            "version": str(e.get("version") or ""),
                            "detail_url": build_detail_url(e),
                        }
                        for e in multi
                    ],
                }
            )
            continue

        if entry is None:
            report.not_in_market.append(
                {"plugin_id": local.id, "local_version": local.version}
            )
            continue

        market_version = str(entry.get("version") or "")
        cmp = compare_versions(local.version, market_version)
        if cmp >= 0:
            report.latest.append(
                {
                    "plugin_id": local.id,
                    "local_version": local.version,
                    "market_id": str(entry.get("id") or ""),
                    "market_version": market_version,
                }
            )
            continue

        report.updates.append(
            UpdateItem(
                plugin_id=local.id,
                name=local.name,
                local_version=local.version,
                local_author=local.author,
                market_id=str(entry.get("id") or ""),
                market_owner=str(entry.get("owner") or ""),
                market_version=market_version,
                market_author=market_author(entry),
                source=build_archive_url(entry),
                detail_url=build_detail_url(entry),
                author_mismatch=check_author_mismatch(local, entry),
            )
        )

    _LAST_REPORT = report
    return report
