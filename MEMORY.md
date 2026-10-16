# MEMORY — qwenpaw-plugin-update-all-third-party

> 第三方插件一键更新插件。0.1.0 起步；0.2.0 幂等保护 + 前端忙碌态 + 限速 0.2s；0.2.1 修 apply 端点 422（FastAPI 签名反射踩坑）；0.2.2 前端弹窗/toast/按钮全面主题适配；0.2.3 卡片顶部「可升级」半透明红条；0.2.4 修「开始更新」按钮初始 disabled 死锁；0.2.5 缓存 TTL 24h；0.3.0 对抗复审修复（M1 改用官方 install_plugin_source / M2 plugin_ids 必填 / m1-m4 + 自更新闭环）；0.3.1 安装前清理 .git 残留（WinError 5）；0.4.0 移除 agent 工具（纯 UI/HTTP 交付）。

## 项目档案

- **作者**：onesixth
- **一句话**：绕开 QwenPaw 官方「全部更新」对第三方插件的三重失明，提供市场精确搜索 + 一键检查/更新 + 商店页按钮注入。
- **依赖**：无外部依赖（httpx / packaging / fastapi 均为宿主自带）。

## 起源：官方「全部更新」机制调查（2026-10-16）

用户发现插件商店「全部更新」精确避开第三方插件。实测（运行环境 2.2.2，repo 源码停在 2.2.0b2——该功能是 2.2.2 新增，bundle 反混淆定位到 `usePluginManager-CXXOypor.js` + `marketPluginIdentity-DxP_IfNr.js`）：

### 机制全貌

- **更新检测**（前端挂载时）：路径 A 官方 catalog（`/api/plugins/catalog` → CDN index → `upgrade_available`）+ 路径 B 社区市场（`/api/plugins/market/search?sort_by=updated_time` 翻页 50×最多20页 → `marketPluginIdentity` 身份匹配）。合并进 `pendingUpdates Map：id → {version, source}`。
- **身份匹配规则**：规范化 id（去@、小写）全等，或「本地 id == 市场 id 短名 && 本地 author ∈ 市场 {owner, developer}」。
- **执行**：逐个 `POST /api/plugins/install {source, force: true}`——后端一把生命周期锁：卸载旧（清理旧工具）→ 装 → 热加载 → agents 后台重载。

### 三道闸门（为什么避开第三方）

1. 官方 catalog 只含官方 CDN 条目（29 个），第三方永远没有 `upgrade_available`；
2. 身份匹配的 author 比对：本地 author 是自由文本（`onesixth`≠`onesixmix`、`Erick`≠`erickcharles`），实测 16 个第三方只有 1 个（archigram）撞上；
3. 市场翻页按 updated_time 拉 240 条，实测第 3 页 30s 超时只拿到 100 条；另有 id 变更/不在市场的情况（approval-sse、ms-chat-history 在市场上叫 `@erickcharles/qwenpaw-ms-server`；dream-skin 早期不在市场）。

### 关键实测数据

- 市场归档直链：`https://platform.agentscope.io/plugins/{owner}/{name}/archive/zip/master`，GET 返回真 zip（PK 头，application/zip）——install 的 source 直接可用。
- 市场搜索 API：`https://platform.agentscope.io/openapi/v1/plugins?search=xx`，响应包一层 `data`（`data.plugins` / `data.total`）。后端 `/api/plugins/market/search` 是透传代理。
- `PluginRegistry` 单例：`qwenpaw/plugins/registry.py`，宿主 lifespan 早期 `set_plugin_http_app(app)` 注入根 FastAPI 实例 → 插件后端**不需要 Request 也能拿 app**（`get_plugin_http_app()`）。这是 agent 工具路径访问 `app.state.plugin_loader` 的官方桥。
- 宿主 install 私有函数 `_post_load_setup` / `_schedule_all_agents_reload` 内部只访问 `request.app.state` → `SimpleNamespace(app=app)` shim 足够。
- 前端插件 frontend JS 以 ESM blob import 进主文档（`usePluginLoader.ts`），非 iframe，可用 `window.QwenPaw.host.{getApiUrl,getApiToken,React,antd}`。
- 插件商店页面路由：`/market?tab=plugins`（MarketplacePage 按 tab 切 AppCenter/PluginManager/SkillMarket）。

## 插件设计要点

- **匹配规则（用户拍板）**：只按市场 id 短名匹配，不做 author 比对；author 差异仅警示。同短名多候选 → 歧义列表，不自动更新。
- **安全阀**：官方 catalog 拉取失败 → check 直接中止报错（防止官方插件被误当第三方覆盖安装）。
- **限速**：市场搜索间隔 0.2s/请求（用户指定，防流量峰值；0.1.0 时为 0.3s）。实测 16 插件全量检查 ~36s（0.3s 版）。
- **幂等保护（0.2.0，用户拍板）**：`_CHECKING`/`_APPLYING` 模块级标志（asyncio 协作式调度下检查+置位之间无 await，无竞态窗口），重复调用抛 `BusyError`（「正在检查插件更新，请等待当前检查完成后再试」/「正在更新插件，请等待当前更新完成后再试」）——**立即提示不排队**。三层落点：web 层 409 + detail；前端 catch `err.busy` 显示 warn toast + 按钮忙碌态；agent 工具层捕获返回 ⏳ 文本。
- **前端忙碌态（0.2.0，用户拍板）**：`refreshToolbarState()` 统一渲染——checking 时检查按钮文本「正在检查更新...」、applying 时更新按钮文本「正在更新...」，两按钮 + 全部卡片更新按钮禁用（opacity 0.6 + not-allowed）。原文本存 `data-qutp-label`。确认弹窗执行中 `data-qutp-closable=0`（点遮罩提示等待）、取消按钮禁用、强制更新勾选框禁用。
- **request 归一化推迟**：`_install_one` 内部才 resolve（全部跳过的批次不要求宿主 app 就绪）。
- **apply 缓存**：复用 24 小时内 check 报告（0.2.5 起，此前 10 分钟）；过期自动重查。前端确认弹窗逐插件串行 POST（天然进度 + 失败隔离）。
- **前端锚点**：面板 `[role="tabpanel"][id$="-panel-installed"]`、工具栏 `[class*="controlActions"]`、卡片 `article[class*="pluginCard"]`（aria-label=插件名）、卡操作区 `[class*="cardActions"]`。注意 antd Tabs 三个 tab 都渲染（CSS 隐藏），必须用 panel id 锚定到已安装 tab。
- **作者不一致提示文案**（用户要求）：弹窗/结果里给「老作者 xx → 新作者 xx（账号 yy），建议前往插件页面人工确认」，confirm_mismatch/勾选框才强制。

## 冒烟记录（0.1.0，2026-10-16）

- 23 个单测全过（unittest discover）。
- 真实环境 check：16 第三方 → 2 可更新（dream-skin 0.7.2→0.8.0、thinking-collapse 1.0.0→2.9.0，均作者不一致）+ 12 已最新 + 2 不在市场 + 0 歧义 0 错误。
- 官方机制下这 16 个全部失明——插件价值实证。

## ⚠️ 0.2.1 踩坑：FastAPI + future annotations + 函数内 Pydantic model = 422（2026-10-17）

**现象**：用户点卡片「更新」报「更新失败：[object Object]，[object Object]」。

**根因链**：
1. `web_api.py` 顶部有 `from __future__ import annotations`（PEP 563，所有注解变字符串）；
2. `ApplyBody` 定义在 `create_router()` 函数体内、`Request` 的 import 也在函数内；
3. FastAPI 解析端点签名时 `get_type_hints` 用**模块级命名空间**解析字符串注解——这两个名字都不在模块级 → 解析失败；
4. FastAPI 把解析失败的参数降级为 query 参数 → `{"loc":["query","body"],"msg":"Field required"}` × 2 → 422；
5. 前端 `new Error(body.detail)`，detail 是数组 → toString = "[object Object],[object Object]"（数组两个元素 = 两个解析失败参数，与截图完全吻合）。

**为什么 GET /check 幸免**：check() 无参数。

**修复**：import（APIRouter/HTTPException/Request）与 `ApplyBody` 全部移到模块级；删除所有 backend 文件的 `from __future__ import annotations`（tools/*.py 连带防 QwenPaw schema 反射同类坑）。

**通用教训**：QwenPaw 插件的 HTTP 端点文件**禁用 future annotations**，Pydantic body 模型与 Request 必须**模块级**。凡框架反射签名的场合（FastAPI 端点、QwenPaw 工具 schema），字符串注解的解析命名空间是模块级——函数内定义的类型注解必炸。

**验证**：TestClient 挂真 router（apply 200 + body 正确解析 + 空 body 200 + 类型错 422）；宿主热重装后 curl 复现场景 → 200 + skipped_mismatch（thinking-collapse，Erick→ErickCharles 提示人工确认）。

## ⚠️ 0.2.2 踩坑：原生 DOM 弹窗白底白字（2026-10-17）

**现象**：暗色主题下「作者不一致」确认弹窗内容全白不可见。

**根因**：弹窗硬编码 `background:#fff` 但文字颜色靠继承——暗色主题全局文字是白色 → 白底白字。0.1.0 写第一版时只对着亮色主题调试，原生 DOM 不像 antd 组件会自动跟随 ConfigProvider 暗色算法。

**修复（两层）**：
1. **语义 CSS 变量**：弹窗/toast/注入按钮全部改用 console 的 `--app-*` token（`styles/tokens.css` 单一来源）：背景 `var(--app-surface,#fff)`、文字 `var(--app-text,…)`、次要文字 `--app-text-secondary`、边框 `--app-border-subtle`、accent `--app-accent` / `--app-accent-border`——主题与 dream-skin 皮肤自动适配。变量挂 `:root`（亮）/`html.dark-mode`（暗），弹窗挂 body 下可继承。
2. **isDark 分支**（变量覆盖不到的语义色）：`document.documentElement.classList.contains('dark-mode')`（ThemeContext 的标记）；toast 四色表分 TOAST_LIGHT/TOAST_DARK（antd dark alert 近似值）；作者不一致警示块暗色用 `rgba(250,173,20,.12)` 底 + `#ffd666` 字。

**待办从 422 修复中继承**：用户刷新页面验证完整流程。

## 0.3.0 对抗复审修复（2026-10-17，复审员 PASS with notes → 全部处置）

复审（无上下文 subagent，task-c93e86607db0，11min）抓出 2 MAJOR + 6 MINOR + 12 NOTE。处置：

- **M1（post-load 双重执行）**：`_load_plugin_with_optional_force_reinstall` 在 force=True 时**内部已构造 before/after 钩子并完整执行 _finish 流程**——旧版手动补调 `_finish_plugin_install_after_load` 致 startup hooks 双跑、agents reload 双调度。修复：`_install_one` 改为直接调**官方 `install_plugin_source(source, app, force=True)`**（plugins.py:668，封装下载→解压→重装→after_load 一条龙；它内部同样用 SimpleNamespace(app=app) shim——官方背书），updater 的下载/解压样板整体删除。**教训：复用宿主私有函数前必须逐行读它的完整内部流程，不能只看签名**。
- **M2（空 body = 全量 apply）**：ApplyBody 全字段默认值 + loopback 免认证 → 本机任意进程无参 POST 即批量覆盖安装（复审实测意外触发：updated=[]，author_mismatch 防线兜住）。修复：web 层 plugin_ids 必填，缺失/空返回 400 + 字符串 detail；全量语义仅保留在 agent 工具路径（third_party_update_apply 省略=全部）。
- **m1**：report_stale 恒 False 死字段 → `_ensure_report` 返回 `(report, from_cache)`，缓存超半程（12h）才置 stale。
- **m2**：批次结束 `invalidate_report()` 作废 `_LAST_REPORT`——已更新项不被重复消费。
- **m3**：compare_versions InvalidVersion 退化路径对齐官方「不等即可更新」（原字符串序会漏报）。
- **m4**：`parse_search_body` 独立函数——市场响应结构漂移抛错进 errors，不再静默误报「不在市场」。
- **m5**：busy 标志注释明确「单事件循环前提」。
- **m6**：前端卡片按钮/横条 title 显式标注 plugin_id（name 匹配同名插件错绑的缓解）。
- **自更新闭环（用户拍板：允许更新自己）**：批次循环运行于旧模块代码对象（不依赖 sys.modules 存活），重装自己后新模块 _APPLYING/_LAST_REPORT 全新初始化、旧 finally 复位旧模块——两代状态各自干净；结果如实进 updated；批次结束统一 invalidate。详见 updater.py 模块 docstring「自更新闭环」。
- 测试 28 → 40：新增 _install_one 走官方 install_plugin_source 断言（恰好抓 M1 的测试）、invalidate 调用断言、stale 真实化、compare 官方语义对齐、parse_search_body 7 例。
- 未修（接受）：NOTE 2 搜索仅首页 20 条（短名匹配固有代价）、NOTE 8 React 重渲染可能覆盖横条 position、NOTE 12 归档 404 消息可读性、测试缺口中 web_api TestClient 层。

## 0.3.1 真实测试发现的第二个坑：.git 残留致 WinError 5（2026-10-17）

真实闭环测试（wait_agent_task 本地降版本 1.0.0 → apply 升回 1.2.0）暴露：安装目录带 **.git 残留**（git 工作树复制安装）时，loader 覆盖拷贝撞上**只读 git objects（444）**→ `WinError 5 拒绝访问`。thinking-collapse 成功是因为目录无 .git。

修复：`_install_one` 安装前精准清理 `get_plugins_dir()/{plugin_id}/.git`（市场归档永不含 .git，删之无损失；其他文件不动——用户数据由 loader delete_files=False 保护），`_force_rmtree` 带 onerror chmod 处理只读位。**教训：真实端到端测试不可替代——winerror 类环境问题只有真跑才暴露。**

真实测试三步全过：① 降版本 → check 报 1.0.0→1.2.0 可更新；② 不带 confirm → skipped_mismatch（跳过路径 ✓）；③ 带 confirm → updated `1.0.0 → 1.2.0`（install_plugin_source 完整链路 ✓），装后磁盘/API 均 1.2.0、.git 已清。

## 0.4.0 移除 agent 工具（2026-10-17，用户拍板）

两个 agent 工具（third_party_update_check / third_party_update_apply）是冗余交付——更新操作只经前端伴生 UI / HTTP 端点触发。拆除：

- plugin.json 删 meta.tools；plugin.py 删 register_tool（仅剩 register_http_router）
- tools/ 目录整体删除；updater 删 `_resolve_request`（None 分支只为 agent 工具存在），`apply_updates(request, ...)` request 必传（web 层唯一调用方）
- force 重装时宿主自动清理：`Removed tools [...] from agents` + governance 注销（日志实测）——**旧工具注销无需手动处理，meta.tools 增删由重装流程闭环**
- 测试 40 → 38（删 _resolve_request ×2），全部通过

## ⚠️ 0.4.x 维护坑：git init 后从开发目录热重装会带 .git（2026-10-17）

`git init` + commit 后，`POST /api/plugins/install {source: 开发目录}` 会把**开发目录的 `.git` 整个拷进安装目录**（loader copytree 不管 gitignore）→ 下次重装撞只读 git objects → WinError 5 死循环。本插件 `_install_one` 的 .git 清理只覆盖自己 apply 的路径，官方 install 端点没有清理。

**标准热重装流程（二选一）**：
1. **git archive → upload（推荐，根治）**：先 commit → `git archive --format=zip -o $env:TEMP\p.zip HEAD` → `curl -F "file=@..." "http://127.0.0.1:8088/api/plugins/upload?force=true"`——archive 只导出已提交内容，天然无 .git。**注意：未 commit 的变更不会被导出**（0.4.0 曾因此回退到 0.3.1，工具回潮）。
2. **手动流程**：install 前先删安装目录 `.git`：`C:\Users\TWD\.qwenpaw\plugins\qwenpaw-plugin-update-all-third-party\.git`（force rmtree 处理只读位）→ install → 装完再删一次（本次拷贝又带入了）。

0.4.0 部署时实测两方案：方案 1 因未 commit 回退 0.3.1（工具回潮教训：archive 版本=HEAD 提交版本）；方案 2 恢复正确，回潮工具被 0.4.0 重装再次注销。

## 收工状态（2026-10-17）

- 全链路（检查 → 跳过 → 确认更新 → 热重装）真实端到端验证通过（wait_agent_task 靶测）
- 0.4.0 移除 agent 工具后宿主日志实证注销闭环；快速审查 CLEAN（1 条 docstring 残留已修）
- git 已初始化并提交 2 笔（feat v0.3.1 / docs README 精简）；**工作区有未提交变更**：v0.4.0 移除工具 + web_api docstring 修复 + README 使用章节前的中间态——待作者 commit
- 后续热重装一律走 `git archive → upload`（见上方维护坑章节），且需先 commit

## 遗留（接受现状）

- 市场归档 404 消息可读性（NOTE 12，原始异常文本直接透出）
- 搜索仅首页 20 条（短名匹配固有代价，歧义兜底）
- React 重渲染可能覆盖横条 position:relative（出现即重刷页面自愈）
