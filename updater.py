# -*- coding: utf-8 -*-
"""updater — 第三方插件更新执行。

对每个目标插件**直接复用宿主官方 install 全流程**：
``qwenpaw.app.routers.plugins.install_plugin_source(source, app, force)``
——它封装了下载市场归档 ZIP → 安全解压 → PluginLoader 强制重装
（先卸载旧版，一把生命周期锁）→ post-load setup（工具/提供者同步
+ agents 重载）完整链路，与官方 POST /api/plugins/install 端点
同一条代码路径。0.2.6 起不再手动拼装下载/解压/_finish 样板——
此前手动补调 _finish_plugin_install_after_load 会导致 post-load
流程双重执行（startup hooks 双跑、agents reload 双调度，复审 M1）。

## 自更新闭环（0.2.6）

本插件自身也可作为更新目标（用户拍板允许），闭环保证：

1. **批次代码连续性**：apply 批次循环运行于旧模块的代码对象上——
   Python 函数执行不依赖 sys.modules 存活，force 重装自己
   （卸载旧实例 → 覆盖拷贝 → 加载新实例）不中断进行中的批次。
2. **状态跨代不污染**：重装后新模块的 ``_APPLYING`` / ``_LAST_REPORT``
   全新初始化（False / None）；旧模块的 finally 复位旧模块自己的
   标志——两代状态各自干净，无跨代残留。
3. **结果如实反馈**：自身更新成功/失败照常进 updated/failed 列表。
4. **缓存一致性**：批次结束统一 ``invalidate_report()``——已更新项
   （含自己）不会残留在缓存里被重复消费；下次操作自动重查。
5. **Web 路由**：执行中的请求持有旧 handler 引用，路由卸载不中断
   进行中的请求；重装完成后宿主挂载新路由，后续请求走新代码。

作者不一致策略（用户拍板）：默认跳过并给出提示文案（老作者 xx、
新作者 xx、建议前往插件页面人工确认）；仅当调用方显式传
``confirm_mismatch=True`` 才更新。
"""

import asyncio
import logging
import os
import shutil
import stat
import time
from typing import Optional

try:
    from .checker import (
        BusyError,
        CheckReport,
        UpdateItem,
        check_all,
        invalidate_report,
        last_report,
    )
except ImportError:  # 兼容 pytest 直接以项目根导入
    from checker import (
        BusyError,
        CheckReport,
        UpdateItem,
        check_all,
        invalidate_report,
        last_report,
    )

logger = logging.getLogger(__name__)

#: apply 复用 check 缓存的最大时长（秒），过期自动重查。24 小时。
APPLY_CACHE_TTL = 86400.0

MISMATCH_HINT_TPL = (
    "作者不一致：本地作者「{local_author}」，市场作者「{market_author}」"
    "（账号 {owner}）。建议先前往插件页面人工确认：{detail_url} 。"
    "本次已跳过；确认无误后可重试并选择强制更新。"
)

#: 更新进行中标志（幂等保护）。原子性前提：web 端点运行在宿主单
#: 事件循环（协作式调度下检查+置位之间无 await 点）；若未来出现
#: 多线程/多 loop 驱动，需改 threading.Lock。
_APPLYING = False


async def _ensure_report(force_refresh: bool) -> tuple[CheckReport, bool]:
    """拿检查报告：缓存有效则复用，否则现场检查。

    Returns:
        (report, from_cache)——from_cache 供 report_stale 真实化
        （0.2.6 前用同一 TTL 二次判断恒为 False 的死字段已废除）。
    """
    if not force_refresh:
        cached = last_report(max_age=APPLY_CACHE_TTL)
        if cached is not None:
            return cached, True
    report = await check_all()
    return report, False


def _force_rmtree(path) -> None:
    """Windows 只读安全的 rmtree（git objects 是 444 只读位）。"""

    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:  # noqa: BLE001
            pass

    shutil.rmtree(path, onerror=_onerror)


async def _install_one(item: UpdateItem, request) -> dict:
    """通过宿主官方 install 全流程热重装单个插件。

    request 为宿主 FastAPI Request（web 层必传）。force 重装（含
    after_load 的 post-load setup 与 agents 重载）全部由
    install_plugin_source 内部完成，此处不再补调 _finish（复审 M1：
    手动补调曾致 post-load 双重执行）。
    """
    from qwenpaw.app.routers import plugins as host_plugins_router

    loader = getattr(request.app.state, "plugin_loader", None)
    if loader is None:
        return {"ok": False, "message": "宿主 plugin_loader 未就绪"}

    # 安装前清理：市场归档永不含 .git；本地安装目录若带 .git（如
    # 从 git 工作树复制安装的残留），loader 覆盖拷贝时撞上只读
    # git objects 会 WinError 5（0.3.1 实测）。精准移除 .git，
    # 不碰其他文件（用户数据由 loader 的 delete_files=False 保护）。
    try:
        from qwenpaw.config.utils import get_plugins_dir

        git_dir = get_plugins_dir() / item.plugin_id / ".git"
        if git_dir.is_dir():
            logger.info("updater: 清理 %s 的 .git 残留", git_dir)
            await asyncio.to_thread(_force_rmtree, git_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("updater: 清理 .git 失败（忽略继续安装）: %s", exc)

    try:
        record = await host_plugins_router.install_plugin_source(
            item.source,
            app=request.app,
            force=True,
        )
        return {
            "ok": True,
            "message": (
                f"{item.plugin_id} {item.local_version} → "
                f"{record.manifest.version}"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("updater 更新 %s 失败: %s", item.plugin_id, exc)
        return {"ok": False, "message": f"{exc}"}


async def apply_updates(
    request,
    plugin_ids: Optional[list[str]] = None,
    confirm_mismatch: bool = False,
    force_refresh: bool = False,
) -> dict:
    """执行第三方插件更新（主入口，web 层调用）。

    Args:
        request: 宿主 FastAPI Request（取 plugin_loader / 触发 agents
            重载用）。仅在实际安装时访问，全跳过批次不要求宿主就绪。
        plugin_ids: 要更新的插件 id 列表；None / 空 = 全部待更新项。
            **web 层（web_api）已强制 plugin_ids 必填**，空 body 400
            拒绝（复审 M2）；本函数保留全量语义供内部复用。
        confirm_mismatch: 是否强制更新作者不一致的候选。
        force_refresh: 是否强制重新检查（忽略缓存）。

    Returns:
        {"updated": [...], "skipped_mismatch": [...], "failed": [...],
         "unknown_ids": [...], "report_stale": bool, "checked_at": float}

    Raises:
        BusyError: 已有更新批次在进行中（幂等保护，提示等待不排队）。
    """
    global _APPLYING

    if _APPLYING:
        raise BusyError("正在更新插件，请等待当前更新完成后再试")
    _APPLYING = True
    try:
        return await _apply_updates_locked(
            request, plugin_ids, confirm_mismatch, force_refresh
        )
    finally:
        _APPLYING = False


async def _apply_updates_locked(
    request,
    plugin_ids: Optional[list[str]],
    confirm_mismatch: bool,
    force_refresh: bool,
) -> dict:
    """apply_updates 的实际执行体（调用方已持有 busy 标志）。"""
    report, from_cache = await _ensure_report(force_refresh)
    stale = from_cache and (
        time.time() - report.checked_at > APPLY_CACHE_TTL / 2
    )

    wanted = {p for p in (plugin_ids or []) if p}
    targets = [
        u
        for u in report.updates
        if not wanted or u.plugin_id in wanted
    ]
    unknown = sorted(wanted - {u.plugin_id for u in report.updates})

    updated: list[dict] = []
    skipped_mismatch: list[dict] = []
    failed: list[dict] = []

    for item in targets:
        if item.author_mismatch and not confirm_mismatch:
            skipped_mismatch.append(
                {
                    "plugin_id": item.plugin_id,
                    "local_author": item.local_author,
                    "market_author": item.market_author,
                    "market_owner": item.market_owner,
                    "detail_url": item.detail_url,
                    "hint": MISMATCH_HINT_TPL.format(
                        local_author=item.local_author or "（空）",
                        market_author=item.market_author,
                        owner=item.market_owner,
                        detail_url=item.detail_url,
                    ),
                }
            )
            continue
        result = await _install_one(item, request)
        entry = {"plugin_id": item.plugin_id, "message": result["message"]}
        (updated if result["ok"] else failed).append(entry)

    # 批次结束作废缓存报告：已更新项不再被后续批次重复消费；含
    # 自更新场景（重装后新模块 _LAST_REPORT 本就为 None，此处对旧
    # 模块实例的清理保证单代内一致）。
    invalidate_report()

    return {
        "updated": updated,
        "skipped_mismatch": skipped_mismatch,
        "failed": failed,
        "unknown_ids": unknown,
        "report_stale": stale,
        "checked_at": report.checked_at,
    }
