# -*- coding: utf-8 -*-
"""third_party_update_apply — 执行第三方插件更新。"""

from typing import List, Optional

try:
    from ..updater import BusyError, apply_updates
except ImportError:  # 兼容 pytest 直接以项目根导入
    from updater import BusyError, apply_updates


def _fmt_list(items: list[dict]) -> str:
    return "；".join(f"{x['plugin_id']}（{x['message']}）" for x in items)


async def third_party_update_apply(
    plugin_ids: Optional[List[str]] = None,
    confirm_mismatch: bool = False,
    force_refresh: bool = False,
) -> str:
    """执行第三方插件更新。按最近一次 third_party_update_check 的结果（24 小时内有效，过期自动重查；force_refresh=True 强制重查）逐个下载市场归档 ZIP 并热重装——流程与官方插件商店安装一致：先卸载旧版再加载新版本，agents 后台自动重载，无需重启宿主。plugin_ids 省略或为空 = 更新全部可更新项；作者不一致的候选默认跳过并返回提示（老作者/新作者/详情页链接，建议人工确认），confirm_mismatch=True 才强制更新。

    Args:
        plugin_ids: 要更新的插件 id 列表（如 ["qwenpaw-plugin-file-tools"]）。
            省略或空列表 = 更新全部可更新的第三方插件。
        confirm_mismatch: 强制更新「作者不一致」的候选。默认 False
            （跳过并给出人工确认建议）。
        force_refresh: 更新前强制重新检查市场（忽略 24 小时缓存）。
    """
    try:
        result = await apply_updates(
            request=None,  # agent 路径：updater 内部经 PluginRegistry 取宿主 app
            plugin_ids=plugin_ids or None,
            confirm_mismatch=confirm_mismatch,
            force_refresh=force_refresh,
        )
    except BusyError as exc:
        return f"⏳ {exc}。"

    lines: list[str] = []
    updated = result.get("updated") or []
    skipped = result.get("skipped_mismatch") or []
    failed = result.get("failed") or []
    unknown = result.get("unknown_ids") or []

    if updated:
        lines.append(f"■ 已更新（{len(updated)} 个）")
        for x in updated:
            lines.append(f"  · {x['plugin_id']}：{x['message']}")
    else:
        lines.append("■ 已更新：无")

    if skipped:
        lines.append(f"\n■ 已跳过——作者不一致（{len(skipped)} 个）")
        for x in skipped:
            lines.append(f"  · {x['plugin_id']}\n      {x['hint']}")

    if failed:
        lines.append(f"\n■ 失败（{len(failed)} 个）")
        for x in failed:
            lines.append(f"  · {x['plugin_id']}：{x['message']}")

    if unknown:
        lines.append(
            f"\n■ 未知的 plugin_ids（不在可更新列表中）：{', '.join(unknown)}"
            "\n  请先用 third_party_update_check 查看当前可更新项。"
        )

    if result.get("report_stale"):
        lines.append("\n（注意：本次使用的是过期检查报告，建议重新 check。）")

    if not updated and not failed and not skipped:
        lines.append("没有需要更新的第三方插件（一切已是最新）。")

    if skipped and not confirm_mismatch:
        lines.append(
            "\n如已人工确认作者变更无误，可带 confirm_mismatch=True 重试更新。"
        )
    return "\n".join(lines)
