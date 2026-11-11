"""
【学习注释版】多智能体旅行规划系统（项目最核心文件）

这个文件做什么？
- 创建 4 个 Agent（景点/天气/酒店/规划）
- 接入高德 MCP 工具
- 按流水线执行 plan_trip()，最终产出 TripPlan

谁调用它？
- trip.py：agent = get_trip_planner_agent(); agent.plan_trip(request)

建议阅读顺序：
1. 四个 PROMPT（角色说明书）
2. __init__（系统怎么组装）
3. plan_trip（一次请求怎么跑）
4. _parse_response（LLM 文本怎么变成对象）
"""

import json
import re
from typing import Dict, Any, List, Optional
from hello_agents import SimpleAgent
from hello_agents.tools import MCPTool
from ..services.llm_service import get_llm
from ..services.rag_service import get_rag_service, KnowledgeChunk
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, Meal, WeatherInfo, Location, Hotel
from ..config import get_settings

# ============ Agent 提示词（System Prompt）============
# 作用：告诉每个 Agent「你是谁、必须怎么做、工具怎么调用」
# 这些不是给人看的注释，而是真正喂给大模型的指令

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""

# 规划 Agent 不调用工具，但必须输出“可被前端渲染”的 JSON 结构
PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "tips": [
    "避坑与预约贴士1(来自知识库真实经验)",
    "特色机位与游玩指南2"
  ],
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}
```

**重要提示:**
1. weather_info数组必须包含每一天的天气信息
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
8. **必须包含 tips 避坑与达人贴士数组**:
   - 提炼3-5条最关键的本地避坑指南、闭馆与门票预约规则、独家拍照机位（优先采用知识库内容）
"""


class MultiAgentTripPlanner:
    """
    多智能体旅行规划系统

    你可以把它理解成“项目经理”：
    - 初始化时组建团队（4 个 Agent + 高德工具）
    - plan_trip() 时按步骤派活并汇总结果
    """

    def __init__(self):
        """
        初始化多智能体系统（昂贵操作，所以外面用单例包一层）

        完成的事情：
        1. 获取 LLM
        2. 创建高德 MCPTool（会启动 amap-mcp-server）
        3. 创建 4 个 SimpleAgent，前 3 个挂工具，规划 Agent 不挂工具
        """
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            settings = get_settings()          # 读 AMAP_API_KEY 等配置
            self.llm = get_llm()               # 共享大模型客户端

            # ---------- 创建共享 MCP 工具（只创建一次，多个 Agent 复用） ----------
            print("  - 创建共享MCP工具...")
            self.amap_tool = MCPTool(
                name="amap",                                  # 工具名前缀
                description="高德地图服务",
                server_command=["uvx", "amap-mcp-server"],    # 启动高德 MCP 子进程
                env={"AMAP_MAPS_API_KEY": settings.amap_api_key},  # 把 Key 传给子进程
                auto_expand=True                              # 展开成多个子工具（POI/天气等）
            )
            self.amap_tool.expandable = True

            # ---------- 景点搜索 Agent ----------
            print("  - 创建景点搜索Agent...")
            self.attraction_agent = SimpleAgent(
                name="景点搜索专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT
            )
            self.attraction_agent.add_tool(self.amap_tool)  # 给它装备高德工具

            # ---------- 天气查询 Agent ----------
            print("  - 创建天气查询Agent...")
            self.weather_agent = SimpleAgent(
                name="天气查询专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT
            )
            self.weather_agent.add_tool(self.amap_tool)

            # ---------- 酒店推荐 Agent ----------
            print("  - 创建酒店推荐Agent...")
            self.hotel_agent = SimpleAgent(
                name="酒店推荐专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT
            )
            self.hotel_agent.add_tool(self.amap_tool)

            # ---------- 行程规划 Agent（纯推理，不直接查地图） ----------
            print("  - 创建行程规划Agent...")
            self.planner_agent = SimpleAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT
            )
            # 注意：这里故意不加工具

            # ---------- RAG 知识检索增强服务 ----------
            print("  - 初始化RAG知识库服务...")
            self.rag_service = get_rag_service()

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")
            print(f"   RAG知识库: 已挂载 {len(self.rag_service.chunks)} 条攻略切片")

        except Exception as e:
            print(f"❌ 多智能体系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        使用多智能体协作生成旅行计划（一次完整业务流水线）

        调用位置：
        - backend/app/api/routes/trip.py -> plan_trip()

        返回：
        - 成功：结构化 TripPlan
        - 异常：fallback 占位行程（保证接口尽量不崩）
        """
        try:
            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            # ===== 步骤1：搜景点 =====
            print("📍 步骤1: 搜索景点...")
            attraction_query = self._build_attraction_query(request)          # 构造给 Agent 的任务文本
            attraction_response = self.attraction_agent.run(attraction_query) # Agent 调工具后返回文本摘要
            print(f"景点搜索结果: {attraction_response[:200]}...\n")

            # ===== 步骤1.5：若用户关注美食，再搜餐饮 POI =====
            food_response = ""
            if self._wants_food_experience(request):
                print("🍜 步骤1.5: 搜索当地美食...")
                food_query = self._build_food_query(request)
                food_response = self.attraction_agent.run(food_query)  # 复用景点 Agent + 高德搜索工具
                print(f"美食搜索结果: {food_response[:200]}...\n")

            # ===== 步骤2：查天气 =====
            print("🌤️  步骤2: 查询天气...")
            weather_query = f"请查询{request.city}的天气信息"
            weather_response = self.weather_agent.run(weather_query)
            print(f"天气查询结果: {weather_response[:200]}...\n")

            # ===== 步骤3：搜酒店 =====
            print("🏨 步骤3: 搜索酒店...")
            hotel_query = f"请搜索{request.city}的{request.accommodation}酒店"
            hotel_response = self.hotel_agent.run(hotel_query)
            print(f"酒店搜索结果: {hotel_response[:200]}...\n")

            # ===== 步骤3.5：RAG 检索本地避坑与深度攻略 =====
            print("💡 步骤3.5: 检索本地专属避坑与深度攻略知识库(RAG)...")
            rag_chunks = []
            rag_context = ""
            try:
                rag_chunks = self.rag_service.search(
                    city=request.city,
                    query=request.free_text_input or "",
                    preferences=request.preferences,
                    top_k=4
                )
                rag_context = self.rag_service.format_for_agent(rag_chunks)
                print(f"RAG 检索完成，命中 {len(rag_chunks)} 条知识切片\n")
            except Exception as e:
                print(f"⚠️ RAG 检索异常(不影响主流程): {e}\n")

            # ===== 步骤4：规划师整合所有材料，输出 JSON 文本 =====
            print("📋 步骤4: 生成行程计划...")
            planner_query = self._build_planner_query(
                request, attraction_response, weather_response, hotel_response, food_response, rag_context
            )
            planner_response = self.planner_agent.run(planner_query)
            print(f"行程规划结果: {planner_response[:300]}...\n")

            # ===== 步骤5：把 LLM 文本解析成 TripPlan 对象 =====
            trip_plan = self._parse_response(planner_response, request, rag_chunks)

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            return trip_plan

        except Exception as e:
            # 流水线任何一步炸了，都退回占位计划，避免前端完全没数据
            print(f"❌ 生成旅行计划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return self._create_fallback_plan(request)

    def _wants_food_experience(self, request: TripRequest) -> bool:
        """
        判断用户是否希望体验当地美食

        依据：
        - preferences 里勾了“美食”
        - 或 free_text_input 里写了美食相关词
        """
        text = f"{request.free_text_input or ''} {' '.join(request.preferences)}"
        food_keywords = ("美食", "小吃", "餐饮", "吃", "赣菜", "特色菜")
        return any(keyword in text for keyword in food_keywords)

    def _build_attraction_query(self, request: TripRequest) -> str:
        """
        构建景点搜索查询

        设计点：
        - 偏好是“美食”时，不要只用“美食”当景点关键词（否则全是餐厅）
        - 优先用非美食偏好；没有则用“著名景点”
        - 查询里直接带上 TOOL_CALL，提高工具调用成功率
        """
        if request.preferences:
            non_food_prefs = [pref for pref in request.preferences if pref != "美食"]
            keywords = non_food_prefs[0] if non_food_prefs else "著名景点"
        else:
            keywords = "著名景点"

        query = (
            f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        )
        return query

    def _build_food_query(self, request: TripRequest) -> str:
        """构建美食搜索查询（单独一步，避免污染景点搜索）"""
        return (
            f"请使用amap_maps_text_search工具搜索{request.city}的特色美食、小吃街和知名餐厅。\n"
            f"[TOOL_CALL:amap_maps_text_search:keywords=特色美食,city={request.city}]"
        )

    def _build_planner_query(
        self,
        request: TripRequest,
        attractions: str,
        weather: str,
        hotels: str = "",
        food: str = "",
        rag_context: str = "",
    ) -> str:
        """
        构建行程规划查询（给规划 Agent 的大 Prompt）

        输入材料：
        - 用户需求（城市/日期/偏好/额外要求）
        - 景点调研结果
        - 天气调研结果
        - 酒店调研结果
        - 可选美食调研结果
        - RAG 检索知识（避坑、预约规则、本地人机位）

        输出期望：
        - 只要 JSON，不要解释文字（方便解析）
        """
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}
"""
        if food:
            query += f"""
**美食信息:**
{food}
"""
        if rag_context:
            query += f"""
{rag_context}
"""
        query += """
**要求:**
1. 每天安排2-3个景点
2. 每天必须包含早中晚三餐,并尽量安排当地特色美食
3. 每天推荐一个具体的酒店(从酒店信息中选择)
4. 考虑景点之间的距离和交通方式
5. 只返回JSON,不要输出其他说明文字
6. 景点的经纬度坐标要真实准确
7. weather_info必须覆盖每一天
"""
        if self._wants_food_experience(request):
            query += "8. 行程中必须突出当地美食体验,餐饮推荐优先使用美食信息中的真实餐厅\n"

        if rag_context:
            query += "9. 【重点避坑与规则】行程必须严格避开上述知识库指出的闭馆时间（如周一全天闭馆），并结合避坑与预约规则在'tips'数组中总结3-5条核心要点！\n"

        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}\n"

        return query

    def _extract_json_str(self, response: str) -> str:
        """
        从模型响应中提取 JSON 字符串

        兼容 3 种常见返回：
        1. ```json ... ```
        2. ``` ... ```
        3. 正文中直接夹着 { ... }
        """
        if "```json" in response:
            json_start = response.find("```json") + 7
            json_end = response.find("```", json_start)
            return response[json_start:json_end].strip()

        if "```" in response:
            json_start = response.find("```") + 3
            json_end = response.find("```", json_start)
            candidate = response[json_start:json_end].strip()
            if candidate.startswith("{"):
                return candidate

        if "{" in response and "}" in response:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            return response[json_start:json_end]

        raise ValueError("响应中未找到JSON数据")

    def _loads_json(self, json_str: str) -> dict:
        """
        解析 JSON，兼容模型常见格式问题（如尾逗号）
        """
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 去掉 }, ] 前面多余逗号：{"a":1,} -> {"a":1}
            cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
            return json.loads(cleaned)

    def _normalize_plan_data(
        self,
        data: dict,
        request: TripRequest,
        rag_chunks: Optional[List[KnowledgeChunk]] = None
    ) -> dict:
        """
        规范化 LLM 返回的数据，避免 Pydantic 校验失败

        例如：
        - 缺字段时补默认值
        - hotel.rating 转成字符串
        - 费用 float 转 int
        - tips 字段补全（若模型未输出，则用 RAG 知识库切片兜底填充）
        """
        data.setdefault("city", request.city)
        data.setdefault("start_date", request.start_date)
        data.setdefault("end_date", request.end_date)
        data.setdefault("overall_suggestions", f"这是为您规划的{request.city}旅行计划。")
        data.setdefault("days", [])
        data.setdefault("weather_info", [])

        # 处理 tips 字段（来自 RAG 检索增强）
        tips = data.get("tips")
        if not tips or not isinstance(tips, list) or len(tips) == 0:
            if rag_chunks:
                # 自动从 RAG 切片中提取出高精度的避坑和游玩贴士
                data["tips"] = [f"[{c.category}] {c.title}: {c.content.splitlines()[0]}" for c in rag_chunks[:4]]
            else:
                data["tips"] = [f"建议提前通过官方渠道查询{request.city}热门景区的放票规则与周一闭馆通知。"]
        else:
            # 过滤空字符串
            data["tips"] = [str(t).strip() for t in tips if str(t).strip()]

        for day in data.get("days", []):
            hotel = day.get("hotel")
            if isinstance(hotel, dict) and hotel.get("rating") is not None:
                hotel["rating"] = str(hotel["rating"])

            for meal in day.get("meals", []):
                cost = meal.get("estimated_cost")
                if isinstance(cost, float):
                    meal["estimated_cost"] = int(cost)

            for attraction in day.get("attractions", []):
                price = attraction.get("ticket_price")
                if isinstance(price, float):
                    attraction["ticket_price"] = int(price)

        return data

    def _parse_response(
        self,
        response: str,
        request: TripRequest,
        rag_chunks: Optional[List[KnowledgeChunk]] = None
    ) -> TripPlan:
        """
        解析 Agent 响应文本 -> TripPlan 对象

        成功路径：extract JSON -> loads -> normalize -> TripPlan(**data)
        失败路径：打日志并走 fallback
        """
        try:
            json_str = self._extract_json_str(response)
            data = self._normalize_plan_data(self._loads_json(json_str), request, rag_chunks)
            trip_plan = TripPlan(**data)  # Pydantic 校验；字段不对会抛错
            return trip_plan

        except Exception as e:
            print(f"⚠️  解析响应失败: {str(e)}")
            print(f"   响应片段: {response[:300]}...")
            print("   将使用备用方案生成计划")
            return self._create_fallback_plan(request, rag_chunks)

    def _create_fallback_plan(
        self,
        request: TripRequest,
        rag_chunks: Optional[List[KnowledgeChunk]] = None
    ) -> TripPlan:
        """
        创建备用计划（当 Agent/解析失败时）

        特点：
        - 保证结构完整，前端还能渲染
        - 景点名会像「南昌景点1」，一眼能看出是兜底结果
        - tips 依然可以利用 RAG 知识库切片
        """
        from datetime import datetime, timedelta

        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")

        days = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)

            day_plan = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天行程",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[
                    Attraction(
                        name=f"{request.city}景点{j+1}",
                        address=f"{request.city}市",
                        # 这里坐标是占位，不是真实地理编码结果
                        location=Location(longitude=116.4 + i*0.01 + j*0.005, latitude=39.9 + i*0.01 + j*0.005),
                        visit_duration=120,
                        description=f"这是{request.city}的著名景点",
                        category="景点"
                    )
                    for j in range(2)
                ],
                meals=[
                    Meal(type="breakfast", name=f"第{i+1}天早餐", description="当地特色早餐"),
                    Meal(type="lunch", name=f"第{i+1}天午餐", description="午餐推荐"),
                    Meal(type="dinner", name=f"第{i+1}天晚餐", description="晚餐推荐")
                ]
            )
            days.append(day_plan)

        fallback_tips = []
        if rag_chunks:
            fallback_tips = [f"[{c.category}] {c.title}: {c.content.splitlines()[0]}" for c in rag_chunks[:3]]
        else:
            try:
                chunks = self.rag_service.search(city=request.city, preferences=request.preferences, top_k=3)
                if chunks:
                    fallback_tips = [f"[{c.category}] {c.title}: {c.content.splitlines()[0]}" for c in chunks]
            except Exception:
                fallback_tips = [f"建议提前关注{request.city}重点景区的官方实名预约规则与开放时间。"]

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=f"这是为您规划的{request.city}{request.travel_days}日游行程,建议提前查看各景点的开放时间。",
            tips=fallback_tips
        )


# ---------------- 单例入口 ----------------
# 为什么单例？
# Agent + MCP 初始化很重，每个请求都新建会又慢又容易出问题
_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """
    获取多智能体旅行规划系统实例（单例模式）

    调用位置：
    - trip.py 的 plan_trip / health_check
    """
    global _multi_agent_planner

    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()

    return _multi_agent_planner
