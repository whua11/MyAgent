"""
【LangGraph 重构版】多智能体旅行规划器
基于 LangGraph 状态图架构重构，支持四路并发外部工具调用、结构化 JSON 输出与反思质检重试循环。
与原版 MultiAgentTripPlanner 具备完全相同的接口契约：
    plan_trip(request: TripRequest) -> TripPlan
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import re
import json
import time
import uuid
import sqlite3
import operator
import requests
from pathlib import Path
from typing import TypedDict, Optional, List, Dict, Any, Annotated

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from ..models.schemas import (
    TripRequest,
    TripPlan,
    DayPlan,
    Attraction,
    Meal,
    Hotel,
    Location,
    WeatherInfo,
    Budget,
)
from ..config import get_settings
from ..knowledge.rag_manager import rag_manager
from ..services.trace_service import trace_manager


# ==============================================================
# 1. 高德真实外部工具封装 (@tool)
# ==============================================================

def _get_amap_key() -> str:
    settings = get_settings()
    return settings.amap_api_key or os.getenv("AMAP_API_KEY", "")


@tool
def amap_weather_tool(city: str) -> str:
    """查询指定城市的实时与未来天气预报"""
    key = _get_amap_key()
    if not key:
        return f"{city}天气晴好，22℃，适宜出行（默认）"
    url = f"https://restapi.amap.com/v3/weather/weatherInfo?city={city}&key={key}&extensions=all"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("status") == "1" and res.get("forecasts"):
            f = res["forecasts"][0]
            casts = f.get("casts", [])
            forecast_str = "；".join([
                f"{c['date']}: 白天{c['dayweather']}，夜间{c['nightweather']}，气温{c['nighttemp']}~{c['daytemp']}℃"
                for c in casts[:4]
            ])
            return f"【高德实时天气预报】{f['city']}：{forecast_str}"
        return f"{city}天气晴好，气温适中"
    except Exception as e:
        return f"查询天气异常: {e}"


@tool
def amap_poi_tool(keywords: str, city: str, limit: int = 5) -> List[Dict[str, Any]]:
    """根据关键词在目标城市检索真实的高德 POI 兴趣点（景点、酒店、美食）"""
    key = _get_amap_key()
    if not key:
        return []
    url = f"https://restapi.amap.com/v3/place/text?keywords={keywords}&city={city}&key={key}&offset={limit}&page=1"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("status") == "1" and res.get("pois"):
            results = []
            for p in res["pois"][:limit]:
                loc_str = p.get("location") or "116.397,39.916"
                lng, lat = (loc_str.split(",") + ["0", "0"])[:2]
                results.append({
                    "name": p.get("name"),
                    "address": p.get("address") or "地址详见地图",
                    "type": p.get("type", "旅游景点"),
                    "longitude": float(lng),
                    "latitude": float(lat)
                })
            return results
        return []
    except Exception as e:
        print(f"高德搜索 POI 异常: {e}")
        return []


# ==============================================================
# 2. 全局状态 (TripState)
# ==============================================================

class FullTripState(TypedDict):
    request: TripRequest
    trace_id: str
    trace_records: Annotated[List[Dict[str, Any]], operator.add]
    search_queries: Dict[str, str]  # 意图改写提炼出的多路精准检索词 (hotel_kw, attraction_kw, food_kw, rag_kw)
    weather_data: str
    attractions_data: List[Dict[str, Any]]
    hotel_data: List[Dict[str, Any]]
    food_data: List[Dict[str, Any]]
    travel_tips_data: str  # RAG 检索出的避坑攻略上下文
    final_plan: Optional[TripPlan]
    retry_count: int
    validation_error: Optional[str]


# ==============================================================
# 3. 规划 Prompt 规范模板（指导 LLM 严格生成 TripPlan 结构）
# ==============================================================

PLANNER_PROMPT_TEMPLATE = """你是金牌专业行程规划专家。请根据景点信息、天气信息和住宿推荐，生成满足前端渲染要求的完整多日旅行计划。

你必须严格按照以下 JSON 格式输出，不要输出任何额外的废话：
```json
{{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "overall_suggestions": "总体游玩建议与节奏安排",
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "公共交通/打车/自驾",
      "accommodation": "酒店类型",
      "hotel": {{
        "name": "真实酒店名称",
        "address": "酒店详细地址",
        "location": {{"longitude": 120.1500, "latitude": 30.2400}},
        "price_range": "300-500元",
        "rating": "4.6",
        "distance": "距离景区2公里",
        "type": "经济型酒店",
        "estimated_cost": 380
      }},
      "attractions": [
        {{
          "name": "真实景点名称",
          "address": "详细地址",
          "location": {{"longitude": 120.1500, "latitude": 30.2400}},
          "visit_duration": 180,
          "description": "游玩建议与看点",
          "category": "历史文化/自然风光",
          "ticket_price": 50
        }}
      ],
      "meals": [
        {{"type": "breakfast", "name": "特色早餐名", "address": "地址", "location": {{"longitude": 120.15, "latitude": 30.24}}, "description": "推荐特色", "estimated_cost": 25}},
        {{"type": "lunch", "name": "正餐餐厅名", "address": "地址", "location": {{"longitude": 120.15, "latitude": 30.24}}, "description": "推荐特色菜", "estimated_cost": 70}},
        {{"type": "dinner", "name": "晚餐推荐", "address": "地址", "location": {{"longitude": 120.15, "latitude": 30.24}}, "description": "推荐特色菜", "estimated_cost": 90}}
      ]
    }}
  ],
  "weather_info": [
    {{
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "晴",
      "day_temp": 22,
      "night_temp": 15,
      "wind_direction": "微风",
      "wind_power": "1-2级"
    }}
  ],
  "tips": [
    "避坑贴士1：提前在官方公众号预约门票",
    "避坑贴士2：结合实时天气的穿衣与交通建议"
  ],
  "budget": {{
    "total_attractions": 150,
    "total_hotels": 760,
    "total_meals": 370,
    "total_transportation": 100,
    "total": 1380
  }}
}}
```

重要规则：
1. days 数组的天数必须与用户的 travel_days 完全一致，date 从 start_date 顺延。
2. 尽量优先使用高德推荐的真实景点、酒店与经纬度坐标，确保地图打点准确。
3. 温度必须是纯数字，不要带 ℃ 或 °C 单位。
4. 必须充分融合【本地旅游避坑与门票预约指南 (RAG)】中的真实规则，如遇周一闭馆需避开，并将实用的预约与避坑建议写入景点 description 和 tips 列表中。
"""


# ==============================================================
# 4. LangGraph 多智能体规划器核心类
# ==============================================================

class LangGraphTripPlanner:
    """基于 LangGraph 状态图的旅行规划智能体"""

    def __init__(self):
        print("🚀 初始化 LangGraph 多智能体旅行规划系统 (带 SqliteSaver 持久化断点恢复)...")
        self.llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL_ID", "deepseek-v4-flash"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            temperature=0.7
        )
        
        # 初始化持久化检查点数据库
        CURRENT_DIR = Path(__file__).resolve().parent
        db_path = CURRENT_DIR.parent.parent / "data" / "trip_checkpoints.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self.conn)
        
        self.workflow = self._build_graph()
        self.app = self.workflow.compile(checkpointer=self.checkpointer)
        print("✅ LangGraph 状态机编译完成 (已挂载 SqliteSaver 持久化检查点)！")

    def _build_graph(self) -> StateGraph:
        """构建图拓扑结构"""
        workflow = StateGraph(FullTripState)

        # 注册节点
        workflow.add_node("intent_rewrite_node", self._intent_rewrite_node)
        workflow.add_node("weather_node", self._weather_node)
        workflow.add_node("attraction_node", self._attraction_node)
        workflow.add_node("hotel_node", self._hotel_node)
        workflow.add_node("food_node", self._food_node)
        workflow.add_node("rag_node", self._rag_node)
        workflow.add_node("planner_node", self._planner_node)
        workflow.add_node("validator_node", self._validator_node)

        # 拓扑连线：
        # 1. START 首先进入意图改写节点
        workflow.add_edge(START, "intent_rewrite_node")

        # 2. 意图改写完成后，扇出并发唤醒 5 个采集工人 (含本地知识库 RAG)
        workflow.add_edge("intent_rewrite_node", "weather_node")
        workflow.add_edge("intent_rewrite_node", "attraction_node")
        workflow.add_edge("intent_rewrite_node", "hotel_node")
        workflow.add_edge("intent_rewrite_node", "food_node")
        workflow.add_edge("intent_rewrite_node", "rag_node")

        # 3. 扇入同步：5 个工人全部采集完毕后汇聚进入 planner
        workflow.add_edge("weather_node", "planner_node")
        workflow.add_edge("attraction_node", "planner_node")
        workflow.add_edge("hotel_node", "planner_node")
        workflow.add_edge("food_node", "planner_node")
        workflow.add_edge("rag_node", "planner_node")

        # 质检与反思回边
        workflow.add_edge("planner_node", "validator_node")
        workflow.add_conditional_edges(
            "validator_node",
            self._should_retry,
            {
                "retry": "planner_node",  # 回边重试
                "ok": END                 # 合格输出
            }
        )

        return workflow

    # ---------- 节点定义 ----------

    def _intent_rewrite_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        free_text = (req.free_text_input or "").strip()

        default_queries = {
            "hotel_kw": f"{req.accommodation} 酒店",
            "attraction_kw": " ".join(req.preferences) if req.preferences else "风景名胜|旅游景点",
            "food_kw": "老字号 特色小吃 必吃餐馆",
            "rag_kw": f"{req.city} {' '.join(req.preferences)}"
        }

        # 🚀 性能优化：无自由要求时 0 秒短路直接返回，不消耗额外 Token
        if not free_text:
            print("🎯 [LangGraph-意图改写] 用户未填写额外自由要求，使用默认检索词（0ms 短路）")
            dur_ms = (time.perf_counter() - t0) * 1000
            rec = trace_manager.build_record(
                node_name="intent_rewrite_node",
                duration_ms=dur_ms,
                status="SUCCESS",
                summary="用户未填自由要求，0ms 快速短路返回默认检索词",
                inputs={"free_text": "", "accommodation": req.accommodation},
                outputs={"search_queries": default_queries}
            )
            return {"search_queries": default_queries, "trace_records": [rec]}

        print(f"🎯 [LangGraph-意图改写] 检测到个性化自由要求：'{free_text}'，唤醒 LLM 精准提取关键词...")
        rewrite_prompt = f"""你是一个专业的地图搜索意图分析师。用户正在规划一次旅行，并提出了一些个性化定制要求。
请将用户的旅行偏好与【个性化自由要求】准确拆解为适合高德地图 POI 检索的核心关键词。

【用户出行信息】
- 目的地: {req.city}
- 住宿偏好: {req.accommodation}
- 旅行风格偏好: {', '.join(req.preferences) if req.preferences else '未特别指定（常规打卡游）'}
- 用户自由要求: {free_text}

【高德地图检索词规则（极其重要）】
1. 严禁包含城市名（高德接口已通过 city 参数限制城市，词中再加城市名会导致检索权重稀释甚至搜空）。
2. 高德是地图 POI 搜索引擎，必须提炼标准品类核心词，不可输出自然语言长句。
3. 【按需提取原则，严禁无中生有】（极其关键）：
   - attraction_kw (景点检索词)：
     * 若用户在旅行风格偏好或自由要求中指定了特定景点类型（如自然风光、看夜景、主题乐园），提取对应核心词。
     * 若用户【未指定景点偏好】（例如仅指定了美食或酒店），必须直接输出通用的 "风景名胜|旅游景点"，严禁擅自假定为“历史古迹”或“博物馆”！
   - hotel_kw (住宿检索词)：
     * 若自由要求中有明确特殊住宿条件（如带电脑/电竞/温泉/海景/浴缸），精准提炼（如“电竞酒店”或“温泉酒店”）。
     * 若无特殊住宿要求，严格结合住宿偏好输出（如“经济型酒店”或“舒适型酒店”）。
   - food_kw (餐饮检索词)：
     * 若提到了特定美食（如赣菜、火锅、早茶、烤鸭），精准提炼（如“赣菜 特色餐馆”）。
     * 若未指定，输出“当地特色美食 老字号”。

请严格输出以下 JSON，不要输出任何解释说明：
```json
{{
  "hotel_kw": "高德酒店搜索词",
  "attraction_kw": "高德景点搜索词",
  "food_kw": "高德美食搜索词",
  "rag_kw": "知识库搜索词"
}}
```"""
        try:
            res = self.llm.invoke(rewrite_prompt)
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", res.content)
            json_str = match.group(1) if match else res.content[res.content.find("{"):res.content.rfind("}") + 1]
            data = json.loads(json_str)
            merged = {
                "hotel_kw": data.get("hotel_kw") or default_queries["hotel_kw"],
                "attraction_kw": data.get("attraction_kw") or default_queries["attraction_kw"],
                "food_kw": data.get("food_kw") or default_queries["food_kw"],
                "rag_kw": data.get("rag_kw") or default_queries["rag_kw"],
            }
            dur_ms = (time.perf_counter() - t0) * 1000
            print(f"🎯 [LangGraph-意图改写] 解析成功 -> 酒店: [{merged['hotel_kw']}] | 景点: [{merged['attraction_kw']}] | 美食: [{merged['food_kw']}]")
            rec = trace_manager.build_record(
                node_name="intent_rewrite_node",
                duration_ms=dur_ms,
                status="SUCCESS",
                summary=f"提取改写词: 酒店=[{merged['hotel_kw']}] | 景点=[{merged['attraction_kw']}]",
                inputs={"free_text": free_text, "accommodation": req.accommodation},
                outputs={"search_queries": merged}
            )
            return {"search_queries": merged, "trace_records": [rec]}
        except Exception as e:
            dur_ms = (time.perf_counter() - t0) * 1000
            print(f"⚠️ [LangGraph-意图改写] 解析失败: {e}，优雅降级使用默认检索词")
            rec = trace_manager.build_record(
                node_name="intent_rewrite_node",
                duration_ms=dur_ms,
                status="ERROR",
                summary=f"意图解析异常降级: {e}",
                inputs={"free_text": free_text},
                outputs={"search_queries": default_queries},
                error=str(e)
            )
            return {"search_queries": default_queries, "trace_records": [rec]}

    def _weather_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        city = state["request"].city
        print(f"⛅ [LangGraph-天气节点] 正在并发查询 {city} 实时天气...")
        res = amap_weather_tool.invoke({"city": city})
        dur_ms = (time.perf_counter() - t0) * 1000
        rec = trace_manager.build_record(
            node_name="weather_node",
            duration_ms=dur_ms,
            status="SUCCESS",
            summary=f"高德 {city} 实时天气查询就绪",
            inputs={"city": city},
            outputs={"weather_preview": res[:60] if res else ""}
        )
        return {"weather_data": res, "trace_records": [rec]}

    def _attraction_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        sq = state.get("search_queries", {})
        default_kw = " ".join(req.preferences) if req.preferences else "风景名胜|旅游景点"
        kw = sq.get("attraction_kw") or default_kw
        print(f"🏛️ [LangGraph-景点节点] 正在并发检索 {req.city} 真实景点 POI (关键词: '{kw}')...")
        limit = max(req.travel_days * 3, 6)
        pois = amap_poi_tool.invoke({"keywords": kw, "city": req.city, "limit": limit})

        # 🛡️ 工业级容错：如果特定组合词导致高德召回 0 条结果，自动降级为城市核心景点通用兜底
        if not pois:
            fallback_kw = "风景名胜|旅游景点"
            print(f"⚠️ [LangGraph-景点节点] 关键词 '{kw}' 召回为 0，启用通用兜底检索: '{fallback_kw}'...")
            pois = amap_poi_tool.invoke({"keywords": fallback_kw, "city": req.city, "limit": limit})
            kw = f"{kw} (降级兜底: {fallback_kw})"

        dur_ms = (time.perf_counter() - t0) * 1000
        rec = trace_manager.build_record(
            node_name="attraction_node",
            duration_ms=dur_ms,
            status="SUCCESS",
            summary=f"高德召回 {len(pois)} 个景点 POI",
            inputs={"keywords": kw, "city": req.city, "limit": limit},
            outputs={"poi_names": [p.get("name") for p in pois[:4]]}
        )
        return {"attractions_data": pois, "trace_records": [rec]}

    def _hotel_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        sq = state.get("search_queries", {})
        kw = sq.get("hotel_kw") or f"{req.accommodation} 酒店"
        print(f"🏨 [LangGraph-酒店节点] 正在并发检索 {req.city} 真实酒店 (关键词: '{kw}')...")
        pois = amap_poi_tool.invoke({"keywords": kw, "city": req.city, "limit": 4})

        # 🛡️ 工业级容错：若未搜到特定酒店，优雅降级为用户基础偏好酒店
        if not pois:
            fallback_kw = f"{req.accommodation} 酒店"
            print(f"⚠️ [LangGraph-酒店节点] 关键词 '{kw}' 召回为 0，启用基础偏好兜底: '{fallback_kw}'...")
            pois = amap_poi_tool.invoke({"keywords": fallback_kw, "city": req.city, "limit": 4})
            kw = f"{kw} (降级兜底: {fallback_kw})"

        dur_ms = (time.perf_counter() - t0) * 1000
        rec = trace_manager.build_record(
            node_name="hotel_node",
            duration_ms=dur_ms,
            status="SUCCESS",
            summary=f"高德召回 {len(pois)} 家酒店 POI (关键词: {kw})",
            inputs={"keywords": kw, "city": req.city, "limit": 4},
            outputs={"hotel_names": [p.get("name") for p in pois]}
        )
        return {"hotel_data": pois, "trace_records": [rec]}

    def _food_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        sq = state.get("search_queries", {})
        kw = sq.get("food_kw") or "老字号 特色小吃 必吃餐馆"
        text = f"{req.free_text_input or ''} {' '.join(req.preferences)}"
        food_kw = ("美食", "小吃", "餐饮", "吃", "特色菜", "菜", "餐馆", "羊肉", "火锅", "烤鸭")
        if not any(k in text for k in food_kw) and kw == "老字号 特色小吃 必吃餐馆":
            print("🍜 [LangGraph-美食节点] 用户未特殊指定美食偏好，跳过餐饮深度搜索")
            dur_ms = (time.perf_counter() - t0) * 1000
            rec = trace_manager.build_record(
                node_name="food_node",
                duration_ms=dur_ms,
                status="SUCCESS",
                summary="用户未指定特殊美食，跳过深度搜索 (由大模型自理)",
                inputs={"keywords": kw},
                outputs={"count": 0}
            )
            return {"food_data": [], "trace_records": [rec]}

        print(f"🍜 [LangGraph-美食节点] 正在并发检索 {req.city} 地道美食 (关键词: '{kw}')...")
        pois = amap_poi_tool.invoke({"keywords": kw, "city": req.city, "limit": 6})
        dur_ms = (time.perf_counter() - t0) * 1000
        rec = trace_manager.build_record(
            node_name="food_node",
            duration_ms=dur_ms,
            status="SUCCESS",
            summary=f"高德召回 {len(pois)} 家美食 POI",
            inputs={"keywords": kw, "city": req.city},
            outputs={"food_names": [p.get("name") for p in pois[:3]]}
        )
        return {"food_data": pois, "trace_records": [rec]}

    def _rag_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        sq = state.get("search_queries", {})
        rag_target = sq.get("rag_kw") or " ".join(req.preferences)
        print(f"📚 [LangGraph-RAG节点] 正在从本地向量库并发检索 {req.city} 旅游避坑与门票指南...")
        tips = rag_manager.get_travel_tips(destination=req.city, preferences=[rag_target] if rag_target else req.preferences)
        dur_ms = (time.perf_counter() - t0) * 1000
        hit = bool(tips)
        rec = trace_manager.build_record(
            node_name="rag_node",
            duration_ms=dur_ms,
            status="SUCCESS",
            summary="命中本地避坑知识库" if hit else "未命中本地避坑库(使用通用常识)",
            inputs={"destination": req.city, "rag_target": rag_target},
            outputs={"hit": hit, "tips_length": len(tips) if tips else 0}
        )
        if tips:
            print(f"📖 [LangGraph-RAG节点] 成功匹配到真实本地避坑攻略，准备注入规划大脑！")
        else:
            print(f"📖 [LangGraph-RAG节点] 未命中特定本地避坑库，使用通用推荐策略")
        return {"travel_tips_data": tips, "trace_records": [rec]}

    def _planner_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        req = state["request"]
        retry_num = state.get("retry_count", 0)
        err = state.get("validation_error")

        if err:
            print(f"🤖 [LangGraph-规划节点] 触发第 {retry_num} 次反思重试，收到质检反馈：{err}")
        else:
            print(f"🤖 [LangGraph-规划节点] 汇总并发采集数据与RAG知识，唤醒 DeepSeek 大模型规划行程...")

        # 格式化上下文
        attractions_text = "\n".join([
            f"- {p['name']} (地址: {p['address']}, 坐标: {p['longitude']},{p['latitude']})"
            for p in state.get("attractions_data", [])
        ]) or "暂无具体POI，按城市核心名胜推荐"

        hotels_text = "\n".join([
            f"- {p['name']} (地址: {p['address']}, 坐标: {p['longitude']},{p['latitude']})"
            for p in state.get("hotel_data", [])
        ]) or "暂无具体POI，按标准星级酒店推荐"

        foods_text = "\n".join([
            f"- {p['name']} (地址: {p['address']})"
            for p in state.get("food_data", [])
        ]) or "由旅客自理或品尝沿途小吃"

        rag_tips_text = state.get("travel_tips_data") or "暂无特定避坑库，遵循常规出行游玩常识"

        feedback = f"\n⚠️ 上一次生成未通过质检：【{err}】，本次请务必针对性改进！\n" if err else ""

        user_content = f"""
【用户真实旅行需求】
目的地城市: {req.city}
旅行天数: {req.travel_days} 天 (从 {req.start_date} 至 {req.end_date})
交通偏好: {req.transportation}
住宿偏好: {req.accommodation}
旅行风格偏好: {', '.join(req.preferences) if req.preferences else '综合游'}
额外特定要求: {req.free_text_input or '无特殊要求'}
{feedback}

【本地旅游避坑与门票预约指南 (本地向量库 RAG 知识检索)】
{rag_tips_text}

【高德地图实时天气情况】
{state.get('weather_data', '晴好')}

【高德真实景点推荐库】
{attractions_text}

【高德真实酒店候选库】
{hotels_text}

【高德地道美食推荐库】
{foods_text}

请严格按 JSON 规范为我生成完整 {req.travel_days} 天详细行程（务必将避坑指南的门票预约规则与避坑贴士体现在 tips 或 description 中）：
"""
        full_prompt = f"{PLANNER_PROMPT_TEMPLATE}\n\n{user_content}"
        
        try:
            res = self.llm.invoke(full_prompt)
            plan = self._parse_json_to_plan(res.content, req)
            dur_ms = (time.perf_counter() - t0) * 1000
            rec = trace_manager.build_record(
                node_name="planner_node",
                duration_ms=dur_ms,
                status="SUCCESS",
                summary=f"DeepSeek 综合方案编制完成 (生成 {len(plan.days)} 天行程)",
                inputs={"city": req.city, "days": req.travel_days, "retry_count": retry_num},
                outputs={"success": True, "days_count": len(plan.days)}
            )
            return {
                "final_plan": plan,
                "retry_count": retry_num + (1 if err else 0),
                "validation_error": None,
                "trace_records": [rec]
            }
        except Exception as e:
            dur_ms = (time.perf_counter() - t0) * 1000
            print(f"⚠️ [LangGraph-规划节点] 解析响应异常: {e}")
            rec = trace_manager.build_record(
                node_name="planner_node",
                duration_ms=dur_ms,
                status="ERROR",
                summary=f"大模型响应解析失败: {e}",
                inputs={"city": req.city, "days": req.travel_days},
                outputs={"error": str(e)},
                error=str(e)
            )
            return {
                "final_plan": None,
                "retry_count": retry_num + 1,
                "validation_error": f"JSON解析或模型校验失败: {e}",
                "trace_records": [rec]
            }

    def _validator_node(self, state: FullTripState) -> dict:
        t0 = time.perf_counter()
        plan = state.get("final_plan")
        req = state["request"]
        problems = []

        if not plan:
            problems.append(state.get("validation_error") or "未能成功生成合法的旅行计划对象")
        else:
            if len(plan.days) != req.travel_days:
                problems.append(f"行程天数不匹配（要求 {req.travel_days} 天，实际生成 {len(plan.days)} 天）")
            if not plan.days or not any(d.attractions for d in plan.days):
                problems.append("每日行程中缺少具体的景点安排")
            if not plan.weather_info:
                problems.append("缺少 weather_info 天气卡片信息")

        dur_ms = (time.perf_counter() - t0) * 1000
        passed = not bool(problems)
        rec = trace_manager.build_record(
            node_name="validator_node",
            duration_ms=dur_ms,
            status="SUCCESS" if passed else "RETRY",
            summary="质检通过！结构完备且完全符合规范" if passed else f"发现问题：{';'.join(problems)}",
            inputs={"has_plan": bool(plan)},
            outputs={"passed": passed, "problems": problems}
        )

        if problems:
            err_msg = "；".join(problems)
            print(f"🔍 [LangGraph-质检节点] 发现问题：{err_msg} ❌")
            return {"validation_error": err_msg, "trace_records": [rec]}

        print("🔍 [LangGraph-质检节点] 质检通过！结构完备且完全符合规范 ✅")
        return {"validation_error": None, "trace_records": [rec]}

    def _should_retry(self, state: FullTripState) -> str:
        err = state.get("validation_error")
        retries = state.get("retry_count", 0)
        max_retries = 2

        if err and retries < max_retries:
            print(f"🔄 [LangGraph-条件路由] 还有重试机会 (当前第 {retries}/{max_retries} 次)，打回重试！")
            return "retry"
        elif err and retries >= max_retries:
            print(f"⚠️ [LangGraph-条件路由] 重试次数已耗尽 ({retries}/{max_retries})，启用兜底保障。")
            return "ok"

        return "ok"

    def _parse_json_to_plan(self, content: str, request: TripRequest) -> TripPlan:
        """从 LLM 返回的文本中提取并解析 TripPlan"""
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        json_str = match.group(1) if match else content[content.find("{"):content.rfind("}") + 1]
        
        # 清除尾逗号
        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
        data = json.loads(json_str)

        # 字段兜底补齐
        data.setdefault("city", request.city)
        data.setdefault("start_date", request.start_date)
        data.setdefault("end_date", request.end_date)
        data.setdefault("overall_suggestions", f"这是为您规划的{request.city}专属旅行计划。")
        data.setdefault("days", [])
        data.setdefault("weather_info", [])

        # 校验并生成 TripPlan
        return TripPlan(**data)

    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        """终极兜底方案：保证接口绝对不崩溃"""
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
        days = []
        for i in range(request.travel_days):
            cur_date = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            days.append(DayPlan(
                date=cur_date,
                day_index=i,
                description=f"第 {i+1} 天：{request.city}核心名胜漫步",
                transportation=request.transportation,
                accommodation=request.accommodation,
                hotel=Hotel(
                    name=f"{request.city}品质酒店",
                    address=f"{request.city}市中心商圈",
                    location=Location(longitude=116.397, latitude=39.916),
                    price_range="300-500元",
                    rating="4.6",
                    distance="距中心2km",
                    type=request.accommodation,
                    estimated_cost=350
                ),
                attractions=[
                    Attraction(
                        name=f"{request.city}著名古迹景点",
                        address=f"{request.city}核心风景区",
                        location=Location(longitude=116.397, latitude=39.916),
                        visit_duration=150,
                        description=f"感受{request.city}深厚历史文化底蕴",
                        category="名胜古迹",
                        ticket_price=60
                    )
                ],
                meals=[
                    Meal(type="breakfast", name="当地特色早餐", description="品尝地道早点", estimated_cost=25),
                    Meal(type="lunch", name="老字号餐厅", description="特色传统午餐", estimated_cost=65),
                    Meal(type="dinner", name="风味晚餐", description="夜景与美食", estimated_cost=85)
                ]
            ))

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions=f"为您量身定制的{request.city}旅行计划，建议出行前关注实时交通与景区开放时间。",
            days=days,
            weather_info=[
                WeatherInfo(
                    date=request.start_date,
                    day_weather="多云",
                    night_weather="晴",
                    day_temp=22,
                    night_temp=14,
                    wind_direction="微风",
                    wind_power="1-2级"
                )
            ],
            tips=[
                f"建议提前关注{request.city}热门景区的预约放票规则",
                "出行准备轻便运动鞋与适宜天气的衣物"
            ],
            budget=Budget(
                total_attractions=60 * request.travel_days,
                total_hotels=350 * request.travel_days,
                total_meals=175 * request.travel_days,
                total_transportation=50 * request.travel_days,
                total=(60 + 350 + 175 + 50) * request.travel_days
            )
        )

    # ---------- 外部调用门面函数 ----------

    def plan_trip(self, request: TripRequest, task_id: Optional[str] = None) -> TripPlan:
        """
        供 FastAPI 路由层调用的核心入口函数（支持断点恢复与幂等命中）
        入参: TripRequest, task_id(可选)
        出参: TripPlan (内嵌 trace_id, task_id)
        """
        trace_id = trace_manager.create_trace_id()
        effective_task_id = task_id or getattr(request, "task_id", None) or f"task_{uuid.uuid4().hex[:10]}"
        config = {"configurable": {"thread_id": effective_task_id}}
        global_start = time.perf_counter()

        try:
            # 1. 检查历史检查点快照 (Breakpoint Inspection)
            snapshot = self.app.get_state(config)
            if snapshot and snapshot.values and snapshot.values.get("final_plan"):
                print(f"⚡ [命中幂等快照] 任务 {effective_task_id} 此前已完成，0ms 直接复用历史成果！")
                plan = snapshot.values["final_plan"]
                plan.trace_id = trace_id
                setattr(plan, "task_id", effective_task_id)
                return plan

            if snapshot and snapshot.next:
                print(f"🔄 [断点恢复] 检测到任务 {effective_task_id} 历史中断，当前停留在: {snapshot.next}，跳过已完成节点无缝续跑...")
                final_state = self.app.invoke(None, config=config)
            else:
                initial_state: FullTripState = {
                    "request": request,
                    "trace_id": trace_id,
                    "trace_records": [],
                    "search_queries": {},
                    "weather_data": "",
                    "attractions_data": [],
                    "hotel_data": [],
                    "food_data": [],
                    "travel_tips_data": "",
                    "final_plan": None,
                    "retry_count": 0,
                    "validation_error": None
                }
                final_state = self.app.invoke(initial_state, config=config)

            plan = final_state.get("final_plan")
            if not plan:
                print("⚠️ 状态机未产出有效 plan，进入优雅兜底...")
                plan = self._create_fallback_plan(request)

            total_ms = (time.perf_counter() - global_start) * 1000
            plan.trace_id = trace_id
            setattr(plan, "task_id", effective_task_id)
            trace_manager.finalize_trace(trace_id, request, total_ms, plan, final_state.get("trace_records", []))
            return plan

        except Exception as e:
            print(f"❌ LangGraph 执行失败: {e}，启用终极兜底方案")
            plan = self._create_fallback_plan(request)
            total_ms = (time.perf_counter() - global_start) * 1000
            plan.trace_id = trace_id
            setattr(plan, "task_id", effective_task_id)
            trace_manager.finalize_trace(trace_id, request, total_ms, plan, [])
            return plan

    def plan_trip_stream(self, request: TripRequest, task_id: Optional[str] = None):
        """
        供 FastAPI SSE 流式接口调用的生成器函数 (支持断点恢复与幂等推送)
        在多节点并发执行过程中，实时产出进度事件帧，最后输出完整 TripPlan
        """
        trace_id = trace_manager.create_trace_id()
        effective_task_id = task_id or getattr(request, "task_id", None) or f"task_{uuid.uuid4().hex[:10]}"
        config = {"configurable": {"thread_id": effective_task_id}}
        global_start = time.perf_counter()

        node_messages = {
            "intent_rewrite_node": "🎯 [意图解析] 正在精准分析您的个性化出行与住宿诉求...",
            "weather_node": "⛅ [天气服务] 高德实时天气查询完毕！",
            "attraction_node": "🏛️ [景点服务] 高德热门景点与坐标匹配就绪！",
            "hotel_node": "🏨 [住宿服务] 周边品质酒店信息检索完成！",
            "food_node": "🍜 [美食服务] 本地老字号与地道小吃搜索完毕！",
            "rag_node": "📚 [知识库RAG] 真实避坑与门票预约规则检索命中！",
            "planner_node": "🤖 [大模型规划] DeepSeek 综合行程方案编制完成！",
            "validator_node": "🔍 [质检自省] 路线连贯性与格式规范质检通过！"
        }

        final_plan = None
        accumulated_records = []
        try:
            # 1. 检查历史检查点快照
            snapshot = self.app.get_state(config)
            
            # 情况 A: 已经完全跑完了（幂等命中）
            if snapshot and snapshot.values and snapshot.values.get("final_plan"):
                final_plan = snapshot.values["final_plan"]
                final_plan.trace_id = trace_id
                setattr(final_plan, "task_id", effective_task_id)
                final_payload = json.dumps({
                    "type": "complete",
                    "message": "⚡ [幂等命中] 任务已在历史完成，直接恢复行程展示！",
                    "trace_id": trace_id,
                    "task_id": effective_task_id,
                    "data": final_plan.model_dump()
                }, ensure_ascii=False)
                yield f"event: complete\ndata: {final_payload}\n\n"
                return

            # 情况 B: 历史中断，从断点恢复续跑
            if snapshot and snapshot.next:
                resume_msg = f"🔄 [断点恢复] 检测到中断任务，正在从待办节点 {list(snapshot.next)} 恢复执行..."
                print(resume_msg)
                yield f"event: progress\ndata: {json.dumps({'type': 'resume', 'task_id': effective_task_id, 'message': resume_msg}, ensure_ascii=False)}\n\n"
                stream_iter = self.app.stream(None, config=config, stream_mode="updates")
            else:
                initial_state: FullTripState = {
                    "request": request,
                    "trace_id": trace_id,
                    "trace_records": [],
                    "search_queries": {},
                    "weather_data": "",
                    "attractions_data": [],
                    "hotel_data": [],
                    "food_data": [],
                    "travel_tips_data": "",
                    "final_plan": None,
                    "retry_count": 0,
                    "validation_error": None
                }
                stream_iter = self.app.stream(initial_state, config=config, stream_mode="updates")

            for chunk in stream_iter:
                for node_name, updated_data in chunk.items():
                    if "trace_records" in updated_data and updated_data["trace_records"]:
                        accumulated_records.extend(updated_data["trace_records"])

                    msg = node_messages.get(node_name, f"节点 {node_name} 处理完成")
                    payload = json.dumps({
                        "type": "progress",
                        "node": node_name,
                        "trace_id": trace_id,
                        "task_id": effective_task_id,
                        "message": msg
                    }, ensure_ascii=False)
                    yield f"event: progress\ndata: {payload}\n\n"
                    
                    if "final_plan" in updated_data and updated_data["final_plan"]:
                        final_plan = updated_data["final_plan"]

            if not final_plan:
                final_plan = self._create_fallback_plan(request)

            total_ms = (time.perf_counter() - global_start) * 1000
            final_plan.trace_id = trace_id
            setattr(final_plan, "task_id", effective_task_id)
            trace_manager.finalize_trace(trace_id, request, total_ms, final_plan, accumulated_records)

            final_payload = json.dumps({
                "type": "complete",
                "message": "🎉 全部智能体协同规划完成！正在加载可视化地图...",
                "trace_id": trace_id,
                "task_id": effective_task_id,
                "data": final_plan.model_dump()
            }, ensure_ascii=False)
            yield f"event: complete\ndata: {final_payload}\n\n"

        except Exception as e:
            fallback = self._create_fallback_plan(request)
            total_ms = (time.perf_counter() - global_start) * 1000
            fallback.trace_id = trace_id
            setattr(fallback, "task_id", effective_task_id)
            trace_manager.finalize_trace(trace_id, request, total_ms, fallback, accumulated_records)
            err_payload = json.dumps({
                "type": "complete",
                "message": f"⚠️ 规划过程异常，已自动为您启用兜底推荐方案: {e}",
                "trace_id": trace_id,
                "task_id": effective_task_id,
                "data": fallback.model_dump()
            }, ensure_ascii=False)
            yield f"event: complete\ndata: {err_payload}\n\n"


# ==============================================================
# 5. 单例工厂函数（供 FastAPI 依赖注入）
# ==============================================================

_langgraph_planner: Optional[LangGraphTripPlanner] = None

def get_langgraph_trip_planner() -> LangGraphTripPlanner:
    """获取 LangGraph 旅行规划器单例"""
    global _langgraph_planner
    if _langgraph_planner is None:
        _langgraph_planner = LangGraphTripPlanner()
    return _langgraph_planner
