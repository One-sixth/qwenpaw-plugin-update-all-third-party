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

无 agent 工具——更新操作只经前端伴生 UI / HTTP 端点触发。
"""

import logging

from qwenpaw.plugins.api import PluginApi

try:
    from .web_api import create_router
except ImportError:  # 兼容 pytest 直接以项目根导入
    from web_api import create_router

logger = logging.getLogger(__name__)

#: HTTP prefix（/api 下的挂载点），插件级唯一。
HTTP_PREFIX = "/update-all-third-party"


class UpdateAllThirdPartyPlugin:
    """第三方插件一键更新插件主类。"""

    def register(self, api: PluginApi) -> None:
        logger.info("注册第三方插件一键更新插件（仅 HTTP，无 agent 工具）...")

        api.register_http_router(
            create_router(),
            prefix=HTTP_PREFIX,
            tags=["update-all-third-party"],
        )

        logger.info("✓ 第三方插件一键更新插件注册完成")


plugin = UpdateAllThirdPartyPlugin()
