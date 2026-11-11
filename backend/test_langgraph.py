"""
LangGraph 极简上手实战：体验多节点状态流转
"""
import sys
import requests
# 兼容 Windows 终端打印 Emoji 与中文字符
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser


AMAP_KEY = os.getenv("AMAP_API_KEY")

@tool
def get_weather_tool(city: str) -> str:
    """查询指定城市的实时与近期天气预报"""
    url = f"https://restapi.amap.com/v3/weather/weatherInfo?city={city}&key={AMAP_KEY}&extensions=all"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("status") == "1" and res.get("forecasts"):
            f = res["forecasts"][0]
            casts = f.get("casts", [])
            # 拼接未来 3 天的真实天气
            forecast_str = "；".join([
                f"{c['date']}: 白天{c['dayweather']}，气温{c['nighttemp']}~{c['daytemp']}℃"
                for c in casts[:3]
            ])
            return f"【高德实时预报】{f['city']}：{forecast_str}"
        return f"{city}天气暂未查询到，默认晴天"
    except Exception as e:
        return f"查询天气异常: {e}"

@tool
def search_poi_tool(keywords: str, city: str, limit: int = 4) -> List[str]:
    """根据关键词在目标城市检索真实的高德 POI 兴趣点（景点、酒店、美食）"""
    url = f"https://restapi.amap.com/v3/place/text?keywords={keywords}&city={city}&key={AMAP_KEY}&offset={limit}&page=1"
    try:
        res = requests.get(url, timeout=5).json()
        if res.get("status") == "1" and res.get("pois"):
            results = []
            for p in res["pois"][:limit]:
                name = p.get("name")
                address = p.get("address") or "地址见地图"
                results.append(f"{name} ({address})")
            return results
        return []
    except Exception as e:
        print(f"高德搜索异常: {e}")
        return []






# ==========================================
# 1. 定义大模型期望返回的 Pydantic 结构
# ==========================================
class TripSummaryPlan(BaseModel):
    summary: str = Field(description="行程总体概述，包含天数和节奏推荐")
    highlights: List[str] = Field(description="核心游玩亮点列表")
    tips: str = Field(description="游玩避坑与实用贴士（结合天气和季节）")

# ==========================================
# 2. 扩充全局状态 (State)
# ==========================================
class TripState(TypedDict):
    city: str
    travel_days: int
    attractions: Optional[List[str]]
    food: Optional[str]
    weather: Optional[str]
    hotel: Optional[str]
    preferences: Optional[List[str]]  
    final_plan: Optional[TripSummaryPlan]  # 👈 结构化输出对象！
    retry_count: int                  # 已重试次数
    validation_error: Optional[str]   # 质检不通过的原因（用来指导重试）


FOOD_KEYWORDS = ("美食", "小吃", "餐饮", "吃", "赣菜", "特色菜")


# ==========================================
# 3. 节点定义：前置采集节点保持并发
# ==========================================
def weather_node(state: TripState) -> dict:
    city = state["city"]
    print(f"⛅ [天气节点] 正在调用高德天气 API 查询 {city}...")
    real_weather = get_weather_tool.invoke({"city": city})
    return {"weather": real_weather}


def attraction_node(state: TripState) -> dict:
    city = state["city"]
    print(f"🏛️ [景点节点] 正在调用高德 POI 检索 {city} 的著名景点...")
    real_attractions = search_poi_tool.invoke({
        "keywords": "著名旅游景点 5A 历史古迹",
        "city": city,
        "limit": 4
    })
    return {"attractions": real_attractions}


def hotel_node(state: TripState) -> dict:
    city = state["city"]
    print(f"🏨 [酒店节点] 正在调用高德 POI 检索 {city} 的住宿...")
    real_hotels = search_poi_tool.invoke({
        "keywords": "特色精品酒店 度假酒店",
        "city": city,
        "limit": 3
     })
    return {"hotel": real_hotels}


def should_search_food(state: TripState) -> str:
    prefs = " ".join(state.get("preferences") or [])
    return "food" if any(k in prefs for k in FOOD_KEYWORDS) else "skip"


def food_node(state: TripState) -> dict:
    if should_search_food(state) == "skip":
        print("🍜 [美食节点] 本次不需要美食，跳过")
        return {}
    city = state["city"]
    print(f"🍜 [美食节点] 正在调用高德 POI 检索 {city} 本地地道美食...")
    real_foods = search_poi_tool.invoke({
        "keywords": "老字号 特色小吃 当地菜",
        "city": city,
        "limit": 3
    })
    return {"food": real_foods}


# ⭐ 核心升级：真正的 LLM 规划节点
def planner_node(state: TripState) -> dict:
    retry_num = state.get("retry_count", 0)
    err = state.get("validation_error")
    if err:
        print(f"🤖 [规划节点] 收到质检修改意见（第 {retry_num} 次重试）：{err}")
    else:
        print(f"🤖 [规划节点] 正在唤醒 DeepSeek 大模型，整合信息并生成结构化规划...")

    # ① 准备解析器与格式指令
    parser = PydanticOutputParser(pydantic_object=TripSummaryPlan)

    feedback = f"\n⚠️ 上一次生成未通过质检：【{err}】，请务必针对性改进！\n" if err else ""

    # ② 组装 Prompt 模板（把前面节点并发搜到的所有数据作为上下文注入）
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一名金牌专业旅行规划师。请基于提供的信息定制旅行方案，并必须严格按照指定格式输出 JSON。\n{format_instructions}"),
        ("user", (
            "目的地：{city}\n"
            "旅行天数：{travel_days} 天\n"
            "当地天气：{weather}\n"
            "候选景点：{attractions}\n"
            "推荐酒店：{hotel}\n"
            "特色美食：{food}\n"
            "{feedback}"
            "请为我生成结构化行程建议。"
        ))
    ])

    # ③ 初始化模型（读取 .env 中的 DeepSeek 配置）
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL_ID", "deepseek-v4-flash"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        temperature=0.7
    )
    
    # ④ 组装标准的 LangChain LCEL 链：Prompt ➔ LLM ➔ 结构化解析
    chain = prompt | llm | parser
    
    # ⑤ 执行推理
    result: TripSummaryPlan = chain.invoke({
        "city": state["city"],
        "travel_days": state["travel_days"],
        "weather": state["weather"],
        "attractions": ", ".join(state["attractions"] or []),
        "hotel": state["hotel"],
        "food": state["food"],
        "feedback": feedback,
        "format_instructions": parser.get_format_instructions()
    })
    
    new_retries = retry_num + (1 if err else 0)
    
    return {
        "final_plan": result,
        "retry_count": new_retries,
        "validation_error": None
    }
    

MAX_RETRIES = 2
def validator_node(state: TripState) -> dict:
    plan = state.get("final_plan")
    problems = []
    if not plan:
        problems.append("未生成有效计划")
    else:
        # 质检规则 1：概述字数（例如至少 60 字）
        if len(plan.summary) < 60:
            problems.append(f"总体概述太简短（仅 {len(plan.summary)} 字，至少 60 字）")
        # 质检规则 2：亮点数量至少 3 条
        if len(plan.highlights) < 3:
            problems.append(f"核心亮点太少（仅 {len(plan.highlights)} 条，至少 3 条）")
        # 质检规则 3：必须有避坑贴士
        if not plan.tips or len(plan.tips) < 20:
            problems.append("避坑贴士不充分或缺失")

    if problems:
        err_msg = "；".join(problems)
        print(f"🔍 [质检节点] 发现问题：{err_msg} ❌")
        return {"validation_error": err_msg}
        
    print("🔍 [质检节点] 质检通过！完美符合要求 ✅")
    return {"validation_error": None}


def should_retry(state: TripState) -> str:
    """条件边：决定是打回重试，还是结束放行"""
    err = state.get("validation_error")
    retries = state.get("retry_count", 0)

    if err and retries < MAX_RETRIES:
        print(f"🔄 [条件路由] 还有重试机会 (当前已重试 {retries}/{MAX_RETRIES})，打回规划节点！")
        return "retry"
    elif err and retries >= MAX_RETRIES:
        print(f"⚠️ [条件路由] 重试次数已用尽 ({retries}/{MAX_RETRIES})，不再重试，直接放行。")

    return "ok"


# ==========================================
# 4. 编排图并连接边 (Graph & Edges)
# ==========================================
# 实例化一个基于 TripState 的图
workflow = StateGraph(TripState)

workflow.add_node("weather_node", weather_node)
workflow.add_node("attraction_node", attraction_node)
workflow.add_node("hotel_node", hotel_node)
workflow.add_node("food_node", food_node)
workflow.add_node("planner_node", planner_node)
workflow.add_node("validator_node", validator_node)

workflow.add_edge(START, "weather_node")          
workflow.add_edge(START, "attraction_node")     
workflow.add_edge(START, "hotel_node")   
workflow.add_edge(START, "food_node")          # 提到第一层，与另外三个并肩

workflow.add_edge("food_node", "planner_node")
workflow.add_edge("weather_node", "planner_node")
workflow.add_edge("hotel_node", "planner_node")
workflow.add_edge("attraction_node", "planner_node")
workflow.add_edge("planner_node", "validator_node")

workflow.add_conditional_edges(
    "validator_node",
    should_retry,
    {
        "retry": "planner_node",  # 👈 往回连！回边成环
        "ok": END                 # 合格或用尽则结束
    }
)

# 编译成可运行的 App
app = workflow.compile()


# ==========================================
# 5. 执行测试
# ==========================================
if __name__ == "__main__":
    initial_input: TripState = {
        "city": "杭州",
        "travel_days": 3,
        "attractions": None,
        "weather": None,
        "hotel": None,
        "food": None,
        "preferences": ["特色菜"], 
        "final_plan": None,
        "retry_count": 0,
        "validation_error": None
    }

    print("\n🚀 开始运行 LangGraph + DeepSeek 状态图...\n")
    final_state = app.invoke(initial_input)
    
    plan: TripSummaryPlan = final_state["final_plan"]
    print("\n" + "="*50)
    print("🎉 最终由 DeepSeek 结构化生成的计划:")
    print(f"【总体概述】\n{plan.summary}\n")
    print(f"【核心亮点】")
    for i, h in enumerate(plan.highlights, 1):
        print(f"  {i}. {h}")
    print(f"\n【避坑贴士】\n{plan.tips}")
    print("="*50)
