# -*- coding: utf-8 -*-
"""third_party_update_check — 检查所有第三方插件可用更新。"""

import datetime

try:
    from ..checker import BusyError, check_all
except ImportError:  # 兼容 pytest 直接以项目根导入
    from checker import BusyError, check_all


def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


async def third_party_update_check() -> str:
    """检查所有第三方插件的可用更新。扫描本地插件目录，跳过官方插件（官方插件由插件商店自带机制负责），逐个向插件市场精确搜索、按市场 id 短名匹配并比较版本，相邻请求间隔 0.2 秒避免流量峰值。返回四类报告：可更新 / 已是最新 / 不在市场 / 歧义候选（同短名多候选，需人工决定），作者不一致的候选带警示标记。同一时刻只允许一个检查在跑，重复调用会返回等待提示。无参数。

    Returns:
        结构化文本报告。可更新项含 market_id、版本变更、source 归档直链与
        author_mismatch 标记；执行更新请用 third_party_update_apply。
    """
    try:
        report = await check_all()
    except BusyError as exc:
        return f"⏳ {exc}。"
    lines: list[str] = []
    lines.append(
        f"第三方插件更新检查报告（{_fmt_ts(report.checked_at)}）"
    )
    lines.append(
        f"本地插件 {report.total_installed} 个：官方 "
        f"{report.official_count} 个（已跳过），第三方 "
        f"{report.third_party_count} 个。"
    )

    if report.updates:
        lines.append(f"\n■ 可更新（{len(report.updates)} 个）")
        for u in report.updates:
            mark = " ⚠️作者不一致" if u.author_mismatch else ""
            lines.append(
                f"  · {u.plugin_id}（{u.name}）："
                f"v{u.local_version} → v{u.market_version}{mark}\n"
                f"      市场 id：{u.market_id}（owner: {u.market_owner}，"
                f"市场作者：{u.market_author}；本地作者：{u.local_author or '（空）'}）\n"
                f"      详情页：{u.detail_url}"
            )
    else:
        lines.append("\n■ 可更新：无")

    if report.latest:
        lines.append(
            f"\n■ 已是最新（{len(report.latest)} 个）："
            + "、".join(
                f"{x['plugin_id']} v{x['local_version']}" for x in report.latest
            )
        )
    if report.not_in_market:
        lines.append(
            f"\n■ 不在市场（{len(report.not_in_market)} 个，无法自动更新）："
            + "、".join(
                f"{x['plugin_id']} v{x['local_version']}"
                for x in report.not_in_market
            )
        )
    if report.ambiguous:
        lines.append(
            f"\n■ 歧义候选（{len(report.ambiguous)} 个，同短名多个市场插件，"
            "需人工决定，不做自动更新）"
        )
        for a in report.ambiguous:
            cand = "；".join(
                f"{c['market_id']} v{c['version']}（{c['owner']}）"
                for c in a["candidates"]
            )
            lines.append(f"  · {a['plugin_id']}（本地 v{a['local_version']}）：{cand}")
    if report.errors:
        lines.append(
            f"\n■ 检查出错（{len(report.errors)} 个）："
            + "、".join(f"{x['plugin_id']}（{x['error']}）" for x in report.errors)
        )

    if report.updates:
        lines.append(
            "\n执行更新：调用 third_party_update_apply（作者不一致的候选默认"
            "跳过并提示人工确认，confirm_mismatch=True 才强制更新）。"
        )
    return "\n".join(lines)
