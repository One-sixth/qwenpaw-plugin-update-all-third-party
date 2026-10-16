# qwenpaw-plugin-update-all-third-party

QwenPaw 第三方插件一键更新插件。

## 背景

QwenPaw 2.2.x 插件商店的「全部更新」机制对第三方插件三重失明（2026-10 实测）：

1. **官方 catalog 闸门**：升级检测只对官方 CDN（download.qwenpaw.agentscope.io）index 里的插件算 `upgrade_available`，第三方插件（约 90% 的装机量）永远不在列表里。
2. **身份匹配闸门**：前端市场路径要求「本地 author ∈ 市场 {owner, developer}」（规范化比对），而本地 plugin.json 的 author 是自由文本（`onesixth` vs `onesixmix`、`Erick` vs `erickcharles`、`0+1+2≠3 Team 115886` vs `115886-cshu`）→ 大量失配。实测 16 个第三方插件只有 1 个恰好撞上。
3. **翻页脆弱闸门**：前端按 `updated_time` 翻页拉全市场（50 条 × 最多 20 页）做匹配，实测第 3 页即 30s 超时，只拉到 100/240 条——翻不到的插件连参与匹配的机会都没有。

三道闸门叠加的结果：「全部更新」实际上 = 只更新官方插件。

## 方案

本插件绕开全部闸门：

- **检查**：扫本地磁盘 manifest → 官方 catalog 过滤（catalog 拉取失败即中止，防官方插件误当第三方覆盖）→ 逐插件市场精确搜索（`search=<本地id>`，0.2s/请求限速）→ **市场 id 短名匹配**（`@owner/name` 的 name 段 == 本地 id，规范化后比对；同短名多候选不自动选，列入歧义）→ semver 比较。
- **更新**：市场归档 ZIP（`platform.agentscope.io/plugins/{owner}/{name}/archive/zip/master`）→ 进程内复用宿主 `qwenpaw.app.routers.plugins` 的完整 install 流程（下载 → 安全解压 → PluginLoader 强制重装 → post-load setup + agents 重载）。
- **作者不一致**：仅作警示不作匹配条件。默认跳过并提示（老作者 xx → 新作者 xx，建议前往插件页面人工确认），`confirm_mismatch=True` 才强制更新。
- **幂等保护**（0.2.0）：同一时刻只允许一个 check / 一个 apply 批次在跑，重复调用立即返回提示（`BusyError`，不排队）：「正在检查插件更新，请等待当前检查完成后再试」/「正在更新插件，请等待当前更新完成后再试」。web 层转 HTTP 409，前端据此显示忙碌态并禁用按钮；agent 工具层捕获后返回友好文本。

## 能力

| 入口 | 能力 |
|------|------|
| 工具 `third_party_update_check` | 检查全部第三方插件更新，四类报告（可更新/已最新/不在市场/歧义） |
| 工具 `third_party_update_apply` | 执行更新（plugin_ids 可选、confirm_mismatch、force_refresh） |
| HTTP `GET /api/update-all-third-party/check` | 前端伴生检查端点 |
| HTTP `POST /api/update-all-third-party/apply` | 前端伴生更新端点 |
| 前端伴生 `frontend/index.js` | 插件商店「已安装插件」页注入「检查第三方更新」「更新全部第三方」按钮 + 版本变更确认弹窗 + 卡片级单独「更新」按钮 |

## 关键实现决策

- **request 归一化推迟到真正安装时**：web 层传真实 FastAPI Request，agent 工具层传 None → `_install_one` 内部经 `PluginRegistry().get_plugin_http_app()`（宿主 lifespan 早期注入的单例桥）构造 `SimpleNamespace(app=app)` shim。宿主 install 流程内部只访问 `request.app.state`，shim 即足够。全跳过批次不要求宿主就绪。
- **前端 DOM 锚点**（2.2.2 bundle 实测）：已安装面板 `[role="tabpanel"][id$="-panel-installed"]`；工具栏 `[class*="controlActions"]`（CSS module 哈希前缀匹配）；卡片 `article[class*="pluginCard"]` + `aria-label`=插件名。
- **前端不依赖官方「全部更新」按钮存在**（它仅在有官方更新时渲染），注入到 `.controlActions` 最前面。
- 复用宿主私有函数的兼容风险由 plugin.json `qwenpaw_version` min/max（2.0~2.3）闸门兜底。
