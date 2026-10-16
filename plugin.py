# -*- coding: utf-8 -*-
"""Update All Third-Party 插件入口。

官方插件商店的「全部更新」对第三方插件三重失明（2026-10 实测）：
  1. 官方 catalog 只含官方 CDN 条目，第三方永远没有 upgrade_available；
  2. 前端身份匹配要求本地 author 与市场 owner/developer 一致，
     自由文本 author 大量失配（onesixth vs onesixmix 等）；
  3. 前端按 updated_time 翻页拉全市场，实测第 3 页即超时。

本插件绕开全部闸门：
  · 检查：扫本地磁盘 manifest → 官方 catalog 过滤 → 逐插件市场
    精确搜索（0.2s 限速）→ 市场id 短名匹配 → semver 比较；
  · 更新：市场归档 ZIP → 宿主官方 install 流程热重装；
  · 前端伴生：插件商店「已安装插件」页注入「检查第三方更新」
    「更新全部第三方」按钮 + 版本变更确认弹窗 + 卡片级单独更新。
"""

import logging

from qwenpaw.plugins.api import PluginApi

try:
    from .tools.check import third_party_update_check
    from .tools.update import third_party_update_apply
    from .web_api import create_router
except ImportError:  # 兼容 pytest 直接以项目根导入
    from tools.check import third_party_update_check
    from tools.update import third_party_update_apply
    from web_api import create_router

logger = logging.getLogger(__name__)

#: HTTP prefix（/api 下的挂载点），插件级唯一。
HTTP_PREFIX = "/update-all-third-party"


class UpdateAllThirdPartyPlugin:
    """第三方插件一键更新插件主类。"""

    def register(self, api: PluginApi) -> None:
        logger.info("注册第三方插件一键更新插件（2 个工具）...")

        api.register_tool(
            tool_name="third_party_update_check",
            tool_func=third_party_update_check,
            description=(
                "检查所有第三方插件的可用更新：扫描本地插件目录，跳过官方"
                "插件，逐个向插件市场精确搜索、按市场 id 短名匹配并比较"
                "版本（限速 0.2s/请求）。返回可更新/已是最新/不在市场/歧义"
                "候选四类报告，作者不一致的候选带警示标记。同一时刻只允许"
                "一个检查在跑，重复调用返回等待提示。"
            ),
            icon="🔎",
            tool_type="network",
            target_param="",
            enabled=True,
        )
        api.register_tool(
            tool_name="third_party_update_apply",
            tool_func=third_party_update_apply,
            description=(
                "执行第三方插件更新：按最近一次检查结果（24 小时内有效，"
                "过期自动重查）逐个下载市场归档 ZIP 并热重装（等价官方"
                "安装流程：先卸载旧版再加载，agents 后台重载）。作者不一致"
                "的候选默认跳过并提示人工确认，confirm_mismatch=True 才"
                "强制更新。同一时刻只允许一个更新批次在跑，重复调用返回"
                "等待提示。"
            ),
            icon="⬆️",
            tool_type="network",
            target_param="",
            enabled=True,
        )

        api.register_http_router(
            create_router(),
            prefix=HTTP_PREFIX,
            tags=["update-all-third-party"],
        )

        logger.info("✓ 第三方插件一键更新插件注册完成（2 个工具）")


plugin = UpdateAllThirdPartyPlugin()
