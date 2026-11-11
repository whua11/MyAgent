# 课堂讲义：HelloAgents 智能旅行助手是怎么运作的

> 目标：读完这份讲义后，你能自己向别人讲清楚：  
> **用户点一下按钮之后，系统里发生了什么。**

---

## 第 0 课：先建立心智模型

把这个项目想成一家「旅行规划公司」，而不是一个网页。

| 角色 | 对应代码 | 干什么 |
|------|----------|--------|
| 前台接待 | `frontend/Home.vue` | 收客户需求（城市、日期、偏好） |
| 业务经理 | `backend/.../trip.py` | 接收订单，交给规划团队 |
| 规划团队负责人 | `MultiAgentTripPlanner` | 安排谁先搜景点、谁查天气、谁做最终行程 |
| 员工 A/B/C | 景点/天气/酒店 Agent | 各自调用高德工具拿真实数据 |
| 总规划师 | 行程规划 Agent | 把材料整合成一份标准行程单（JSON） |
| 展示大厅 | `frontend/Result.vue` | 把行程单漂亮地展示出来 |

**核心思想只有一句：**

> LLM 负责「思考和整理」，MCP/高德负责「查真实世界信息」，前后端负责「收需求、展示结果」。

如果你只记住一句，记住这句就够了。

---

## 第 1 课：这个项目到底解决什么问题？

### 传统做法有什么痛点？

如果只用一个大模型聊天：

- 你问「南昌三天怎么玩」，模型可能编造不存在的店
- 天气、地址、经纬度都可能是假的
- 前端没法稳定渲染（因为返回的是散文，不是结构化数据）

### 这个项目怎么解决？

1. **用工具查真实数据**（高德 MCP）
2. **用多个 Agent 分工**（搜景点、查天气、找酒店、做规划）
3. **强制输出 JSON**（前端能直接渲染）
4. **做成完整产品**（有表单页、结果页、地图、预算）

所以它不是「ChatGPT 套壳」，而是一个 **Agent 应用工程**。

---

## 第 2 课：从用户点击开始，跟一遍完整链路

假设用户填写：

- 城市：南昌
- 日期：3 天
- 偏好：美食
- 额外要求：体验当地美食

然后点击「开始规划我的旅行」。

### 步骤 A：前端收集并发送请求

文件：`frontend/src/views/Home.vue`

关键代码逻辑：

1. 把日期格式化成 `YYYY-MM-DD`
2. 组装成请求对象：
   - `city`
   - `start_date` / `end_date`
   - `travel_days`
   - `transportation`
   - `accommodation`
   - `preferences`
   - `free_text_input`
3. 调用 `generateTripPlan(requestData)`

文件：`frontend/src/services/api.ts`

它真正做的是：

```text
POST http://localhost:8000/api/trip/plan
Content-Type: application/json
Body: 上面的请求对象
```

超时设成了 120 秒，因为后端要多次调 MCP 和 LLM，本来就慢。

### 步骤 B：后端 API 接单

文件：`backend/app/api/routes/trip.py`

流程：

1. FastAPI 把 JSON 自动校验成 `TripRequest`（Pydantic）
2. 调用 `get_trip_planner_agent()` 拿到规划器（单例）
3. 调用 `agent.plan_trip(request)`
4. 包装成：

```json
{
  "success": true,
  "message": "旅行计划生成成功",
  "data": { ...TripPlan... }
}
```

### 步骤 C：多 Agent 流水线开工

文件：`backend/app/agents/trip_planner_agent.py` 的 `plan_trip()`

顺序执行：

1. 景点 Agent 搜景点
2. （可选）再搜美食
3. 天气 Agent 查天气
4. 酒店 Agent 搜酒店
5. 规划 Agent 汇总成 JSON
6. `_parse_response()` 把文本变成 `TripPlan` 对象

### 步骤 D：前端展示

文件：`frontend/src/views/Home.vue` + `Result.vue`

1. 成功后把 `data` 存进 `sessionStorage`
2. 跳转到 `/result`
3. `Result.vue` 读取并渲染：
   - 每日行程
   - 预算
   - 天气
   - 地图标记
   - 景点图片（再请求 `/api/poi/photo`）

到这里，一次完整业务闭环结束。

---

## 第 3 课：为什么要「多 Agent」，而不是一个 Agent？

这是面试和理解项目最关键的一点。

### 一个 Agent 全包会怎样？

你给一个 Agent 说：

> 「你既要搜景点，又要查天气，又要找酒店，又要输出标准 JSON。」

常见问题：

- Prompt 太长，模型容易漏步骤
- 工具调用混乱（该查天气时去搜酒店）
- 不好调试（不知道哪一步错了）
- 不好扩展（加一个「交通 Agent」会更乱）

### 多 Agent 的好处

本项目拆成 4 个专家：

| Agent | 职责 | 是否用工具 |
|-------|------|-----------|
| 景点搜索专家 | 找景点 POI | 是（高德） |
| 天气查询专家 | 查天气预报 | 是（高德） |
| 酒店推荐专家 | 找住宿 | 是（高德） |
| 行程规划专家 | 整合材料，输出 JSON | 否（纯推理） |

这叫 **职责分离（Separation of Concerns）**。

你可以把它理解成：

- 前 3 个 Agent = 「调研员」
- 第 4 个 Agent = 「写报告的人」

调研员负责拿事实，写报告的人负责组织成可交付结果。

---

## 第 4 课：Agent 是什么？在本项目里长什么样？

### 概念层

一个 Agent 通常包含：

1. **LLM**：会说话、会推理的大脑
2. **System Prompt**：告诉它「你是谁、该怎么做」
3. **Tools（可选）**：它能调用的外部能力
4. **run(query)**：你给它一个任务，它返回结果

### 代码层

在 `trip_planner_agent.py` 的 `__init__` 里：

```python
self.attraction_agent = SimpleAgent(
    name="景点搜索专家",
    llm=self.llm,
    system_prompt=ATTRACTION_AGENT_PROMPT
)
self.attraction_agent.add_tool(self.amap_tool)
```

含义：

- `name`：给人看的名字
- `llm`：共用同一个大模型客户端
- `system_prompt`：角色设定 + 工具调用格式要求
- `add_tool`：给它装备高德工具

天气 Agent、酒店 Agent 同理。  
规划 Agent **不 add_tool**，因为它只做整合，不查地图。

---

## 第 5 课：System Prompt 为什么这么重要？

以景点 Agent 的 Prompt 为例，它做了三件事：

1. **定角色**：你是景点搜索专家
2. **定行为约束**：必须用工具，不许编造
3. **定调用格式**：

```text
[TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]
```

为什么要强制格式？

因为 HelloAgents 的 SimpleAgent 需要从模型输出里识别「这是一次工具调用」，然后真正去执行 MCP 工具。

如果模型只回复：

> 「我觉得滕王阁不错」

那就没有真实查询，数据不可靠。

### 规划 Agent 的 Prompt 更特殊

它不教工具调用，而是教 **输出 schema**：

- 必须有 `days`
- 每天要有 `attractions` / `meals` / `hotel`
- 必须有 `weather_info`
- 必须有 `budget`
- 温度要是数字，不要写 `34°C`

这是为了让返回结果能被 `TripPlan(**data)` 校验通过。

**结论：**  
Prompt 不是「装饰」，而是这个系统的「业务规则说明书」。

---

## 第 6 课：MCP 是什么？高德是怎么被调用的？

### 先理解 MCP（Model Context Protocol）

你可以把它理解成：

> Agent 调用外部工具的「标准插座」。

以前每个工具都要单独写适配器；有了 MCP，只要服务端遵守协议，Agent 就能统一调用。

### 本项目怎么接高德？

```python
self.amap_tool = MCPTool(
    name="amap",
    description="高德地图服务",
    server_command=["uvx", "amap-mcp-server"],
    env={"AMAP_MAPS_API_KEY": settings.amap_api_key},
    auto_expand=True
)
```

拆开讲：

| 参数 | 含义 |
|------|------|
| `server_command` | 启动一个高德 MCP 子进程：`uvx amap-mcp-server` |
| `env` | 把高德 Key 传给这个子进程 |
| `auto_expand=True` | 把一个大工具拆成很多子工具（POI搜索、天气等） |

运行时你会在日志里看到：

```text
📝 使用 Stdio 传输 (命令): uvx amap-mcp-server
🔗 连接到 MCP 服务器...
✅ 连接成功！
```

意思是：后端临时拉起一个地图工具服务，通过标准输入输出对话，查完再断开。

### Agent 调工具时发生了什么？

以搜景点为例：

1. 代码构造 query，甚至直接带上工具调用标记
2. `attraction_agent.run(query)`
3. Agent/框架识别到要调用 `amap_maps_text_search`
4. MCP 服务去请求高德 API
5. 返回真实 POI 列表（名称、地址等）
6. Agent 再把结果整理成自然语言摘要，返回给 `plan_trip`

所以：**景点不是 LLM 编的，是查出来的。**

---

## 第 7 课：`plan_trip()` 逐步精讲（最核心）

打开 `trip_planner_agent.py`，跟着 `plan_trip()` 走。

### Step 1：搜景点

```python
attraction_query = self._build_attraction_query(request)
attraction_response = self.attraction_agent.run(attraction_query)
```

`_build_attraction_query()` 的聪明点：

- 如果偏好是「美食」，**不会直接用「美食」当景点关键词**
- 会优先用其他偏好；没有则用「著名景点」

为什么？  
因为如果关键词是「美食」，高德会返回大量餐厅，行程会变成「只会吃、没有玩」。

### Step 1.5：如果用户要美食，再搜一次

```python
if self._wants_food_experience(request):
    food_response = self.attraction_agent.run(food_query)
```

`_wants_food_experience()` 看偏好和额外要求里有没有：

- 美食 / 小吃 / 餐饮 / 吃 / 赣菜 / 特色菜

这就是业务规则：  
**景点搜索和美食搜索分开，最后再合并给规划师。**

### Step 2：查天气

```python
weather_response = self.weather_agent.run(f"请查询{request.city}的天气信息")
```

天气 Agent Prompt 会逼它调用 `amap_maps_weather`。

### Step 3：搜酒店

```python
hotel_response = self.hotel_agent.run(
    f"请搜索{request.city}的{request.accommodation}酒店"
)
```

会把「经济型酒店 / 舒适型酒店」等偏好带进去。

### Step 4：规划师整合

```python
planner_query = self._build_planner_query(...)
planner_response = self.planner_agent.run(planner_query)
```

这里把前面所有调研结果塞进一个大 Prompt：

- 基本信息
- 景点信息
- 天气信息
- 酒店信息
- （可选）美食信息
- 额外要求

并明确要求：

- 只返回 JSON
- 每天 2～3 个景点
- 每天三餐
- 天气覆盖每一天
- 坐标尽量真实

### Step 5：解析成对象

```python
trip_plan = self._parse_response(planner_response, request)
```

解析流水线：

1. `_extract_json_str`：从 markdown/纯文本里抠出 JSON
2. `_loads_json`：解析；失败则尝试清掉尾逗号再解析
3. `_normalize_plan_data`：补默认字段、修类型（如 rating 转字符串）
4. `TripPlan(**data)`：Pydantic 校验
5. 全失败则 `_create_fallback_plan`：生成占位行程，避免接口彻底挂掉

**这就是工程化思维：**  
模型不稳定是常态，系统必须有兜底。

---

## 第 8 课：数据结构为什么这么重要？

文件：`backend/app/models/schemas.py`

### 输入：`TripRequest`

前端和后端的「合同」。  
字段不对，FastAPI 会直接 422，不会进业务逻辑。

### 输出：`TripPlan`

前端渲染依赖这些字段：

- `days[].attractions[].location.longitude/latitude` → 地图打点
- `days[].meals` → 餐饮列表
- `budget` → 预算卡片
- `weather_info` → 天气区
- `overall_suggestions` → 顶部建议

如果 Planner 返回散文，前端就废了。  
所以本项目坚持：**最后一公里必须是结构化数据。**

---

## 第 9 课：配置系统怎么工作？

文件：`backend/app/config.py` + `backend/.env`

### 两类关键配置

1. **LLM 配置**（给 HelloAgentsLLM）
   - `LLM_API_KEY`
   - `LLM_MODEL_ID`
   - `LLM_BASE_URL`
2. **高德配置**（给 MCP）
   - `AMAP_API_KEY`

注意：

- `Settings` 会读 `AMAP_API_KEY` 到 `settings.amap_api_key`
- `HelloAgentsLLM()` 主要直接读环境变量 `LLM_*`
- 启动时 `validate_config()` 会检查高德 Key 是否存在

前端还有单独的：

- `VITE_API_BASE_URL`：后端地址
- `VITE_AMAP_WEB_JS_KEY`：浏览器地图渲染 Key

**后端 Key** 和 **前端 JS Key** 用途不同：

- 后端：服务端调 Web 服务 API（MCP）
- 前端：浏览器加载地图 SDK

---

## 第 10 课：前端怎么把结果「变成页面」？

### 1）Home 页：收集需求

- 日期变化时自动算 `travel_days`
- 提交时显示假进度条（到 90% 会卡住，因为真实后端更慢）
- 成功后把结果放 `sessionStorage`

为什么用 `sessionStorage`？

- 简单，不需要全局状态库
- 刷新结果页还能暂时保留
- 关闭标签页会清掉（适合演示型项目）

### 2）Result 页：展示

`onMounted` 时：

1. 读 `sessionStorage.tripPlan`
2. `loadAttractionPhotos()` 逐个请求图片
3. `initMap()` 用高德 JS API 画地图
4. 按景点坐标计算中心点并 `setFitView`

你会看到结果页有：

- 行程概览
- 预算明细
- 景点地图
- 每日折叠行程
- 天气信息

这就是「结构化输出」的价值：同样一份 JSON，前端可以拆成很多模块展示。

---

## 第 11 课：单例模式为什么到处都是？

你会看到：

- `get_llm()`
- `get_trip_planner_agent()`
- `get_amap_service()`

都用了「全局变量 + 第一次创建」的单例模式。

原因：

1. Agent 初始化很贵（要连 MCP、展开工具）
2. LLM 客户端没必要每次新建
3. 同一次服务进程里复用更稳、更快

所以第一次请求会慢一点（初始化），后面会好一些。

---

## 第 12 课：一次请求里，钱和时间花在哪？

### 时间主要花在：

1. 多次启动/连接 MCP（景点、美食、天气、酒店各自可能连一次）
2. 多次 LLM 推理（每个 Agent 至少一轮）
3. Planner 生成长 JSON

所以 2～3 分钟并不奇怪。

### 调用成本主要花在：

- LLM token（尤其 Planner Prompt 很长）
- 高德 API 调用次数

这也解释了为什么工程上会考虑：

- 并行化 Agent
- 复用 MCP 连接
- 缓存同城天气

（这些是扩展方向，当前代码是串行流水线。）

---

## 第 13 课：怎么自己「读代码学会」？

请按这个顺序，边读边在纸上画箭头：

### Day 1（理解链路）

1. `Home.vue` 的 `handleSubmit`
2. `api.ts` 的 `generateTripPlan`
3. `trip.py` 的 `plan_trip`
4. `trip_planner_agent.py` 的 `plan_trip`

目标：能口述「请求从哪到哪」。

### Day 2（理解 Agent）

1. 四个 System Prompt
2. `__init__` 里如何创建 Agent 和 MCPTool
3. `_build_attraction_query` / `_build_food_query` / `_build_planner_query`

目标：能解释「为什么拆 4 个 Agent」。

### Day 3（理解工程）

1. `schemas.py`
2. `_parse_response` 相关函数
3. `Result.vue` 如何渲染 `TripPlan`

目标：能解释「为什么必须输出 JSON」。

### 自测题（建议你口头答）

1. 用户点提交后，第一个后端函数是谁？
2. 景点数据和天气数据分别从哪来？
3. 规划 Agent 为什么不需要工具？
4. 如果 LLM 返回 ```json ... ```，系统怎么处理？
5. 如果解析失败，用户会看到什么？
6. 前端地图用的是哪类 Key？和后端 Key 有什么区别？

---

## 第 14 课：常见误区纠正

### 误区 1：这是一个聊天机器人

不是。它是「工作流 + 工具调用 + 结构化交付」。

### 误区 2：多 Agent 只是套娃喊几个名字

不是。每个 Agent 有独立 Prompt、独立职责，有的有工具、有的没有。

### 误区 3：MCP 可有可无

没有 MCP/高德，这个项目就退化成「模型瞎编旅行攻略」。

### 误区 4：前端不重要

前端决定产品可演示性。没有结果页和地图，面试官很难感知价值。

### 误区 5：fallback 是失败

fallback 是工程保护。面试时可以说：

> 我知道模型输出不稳定，所以设计了解析容错和兜底方案，保证接口可用性。

---

## 第 15 课：如果你要改这个项目，该从哪改？

按难度排序：

### 初级

- 改 Planner Prompt，让餐饮更本地化
- 增加偏好标签（亲子、夜景）
- 结果页增加「复制行程」按钮

### 中级

- 把景点/天气/酒店 Agent 并行执行（缩短耗时）
- 增加交通路线 Agent
- 把 fallback 做得更智能（至少用地图默认中心）

### 高级

- 引入记忆：用户历史偏好
- 引入 RAG：城市攻略文档
- 增加评价反馈闭环：用户改行程后再规划

---

## 第 16 课：用一个比喻收尾

把整个系统再压缩成一句话：

> 前端是柜台，API 是前台，多 Agent 是团队，MCP 是查外部资料的电话，LLM 是写报告的人，JSON 是标准表格，结果页是装裱好的方案书。

当你能不看笔记把这句话展开成 2 分钟讲解时，你就已经真正「学会」这个项目了。

---

## 附录 A：关键文件速查表

| 文件 | 一句话作用 |
|------|------------|
| `frontend/src/views/Home.vue` | 收需求并发起规划 |
| `frontend/src/services/api.ts` | 调后端 API |
| `frontend/src/views/Result.vue` | 展示行程与地图 |
| `backend/app/api/main.py` | FastAPI 应用入口 |
| `backend/app/api/routes/trip.py` | `/api/trip/plan` |
| `backend/app/agents/trip_planner_agent.py` | 多 Agent 核心 |
| `backend/app/models/schemas.py` | 输入输出数据合同 |
| `backend/app/services/llm_service.py` | LLM 单例 |
| `backend/app/services/amap_service.py` | 高德服务封装 |
| `backend/app/config.py` | 配置与校验 |
| `backend/.env` | 密钥 |

## 附录 B：建议你边跑边看的日志信号

后端日志里重点看：

1. `收到旅行规划请求`
2. `开始初始化多智能体...`
3. `连接成功！`（MCP）
4. `步骤1/2/3/4`
5. `旅行计划生成完成`
6. `POST /api/trip/plan 200`

如果某一步卡住，就知道瓶颈在 MCP 还是 LLM。

---

*讲义对应仓库：`helloagents-trip-planner`*  
*建议配合 `PROJECT_GUIDE.md` 一起看：前者偏「怎么写简历」，本讲义偏「怎么真正学会」。*
