# -*- coding: utf-8 -*-
"""web_api — 前端伴生 HTTP 端点。

路由挂载：``register_http_router(create_router(), prefix=...)``
→ ``GET  /api/<prefix>/check``
→ ``POST /api/<prefix>/apply``  body: {"plugin_ids": [...],
   "confirm_mismatch": bool, "force_refresh": bool}

前端伴生脚本（frontend/index.js）在插件商店页注入按钮后调用这两
个端点；agent 工具（tools/）走同一份 checker/updater 逻辑。

⚠️ 签名反射约束：FastAPI 解析端点签名依赖模块级命名空间——
Pydantic body 模型与 Request 等 import 必须放模块级，且本模块
禁用 ``from __future__ import annotations``（PEP 563 字符串注解
在 get_type_hints 解析时若名字不在模块级命名空间会降级为 query
参数 → 422 missing，0.2.1 实测踩坑：ApplyBody 定义在函数内部 +
future annotations，apply 端点 100% 复现 422）。

⚠️ apply 暴露面（复审 M2）：宿主 loopback 默认免认证，本端点
要求 ``plugin_ids`` 必填——空 body 一律 400 拒绝，杜绝「无参一次
POST = 批量覆盖安装全部插件」的放大破坏面。全量语义仅保留在受
信任的 agent 工具路径（third_party_update_apply）。
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    from . import checker, updater
except ImportError:  # 兼容 pytest 直接以项目根导入
    import checker
    import updater

logger = logging.getLogger(__name__)


class ApplyBody(BaseModel):
    """POST /apply 请求体。"""

    plugin_ids: List[str] = []
    confirm_mismatch: bool = False
    force_refresh: bool = False


def create_router():
    """构造 FastAPI 路由。"""
    router = APIRouter()

    @router.get("/check")
    async def check() -> dict:
        """检查全部第三方插件更新（无副作用，只读 + 报告缓存）。

        幂等：已有检查在跑时返回 409 + {"detail": 提示}，前端据此
        提示「正在检查更新，请等待」而非重复发起。
        """
        try:
            report = await checker.check_all()
        except checker.BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return report.to_dict()

    @router.post("/apply")
    async def apply(body: ApplyBody, request: Request) -> dict:
        """执行更新（逐插件热重装，见 updater.apply_updates）。

        plugin_ids 必填（复审 M2）：缺失/空列表返回 400——禁止空
        body 触发全量更新。幂等：已有更新批次在跑时返回 409 +
        {"detail": 提示}。
        """
        ids: Optional[List[str]] = [p for p in body.plugin_ids if p]
        if not ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    "plugin_ids 必填：请显式列出要更新的插件 id。"
                    "全量更新请先 GET /check 并从返回的 updates 中选择。"
                ),
            )
        try:
            return await updater.apply_updates(
                request,
                plugin_ids=ids,
                confirm_mismatch=body.confirm_mismatch,
                force_refresh=body.force_refresh,
            )
        except updater.BusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
