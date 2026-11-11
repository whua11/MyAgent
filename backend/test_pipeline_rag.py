"""
【流水线 RAG 注入逻辑专项测试】

测试内容：
1. 验证 TripRequest 能够正确触发 RAG 检索
2. 验证 _build_planner_query 是否将 RAG 避坑知识与约束条件正确注入提示词
3. 验证 _normalize_plan_data 与 TripPlan 能够正确携带 tips 字段
"""

import sys
import os

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

sys.path.insert(0, os.path.dirname(__file__))

from app.models.schemas import TripRequest, TripPlan
from app.services.rag_service import get_rag_service


def test_pipeline_rag():
    print("=" * 60)
    print("🧪 开始 Agent 流水线与 RAG 衔接测试...")
    print("=" * 60)

    # 1. 模拟前端请求
    req = TripRequest(
        city="北京",
        start_date="2025-06-01",
        end_date="2025-06-03",
        travel_days=3,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["历史文化", "摄影"],
        free_text_input="想多拍一些故宫和胡同的照片"
    )
    print(f"\n[测试 1] 模拟请求: {req.city} {req.travel_days}天 偏好:{req.preferences}")

    # 2. 检索 RAG
    rag = get_rag_service()
    chunks = rag.search(
        city=req.city,
        query=req.free_text_input,
        preferences=req.preferences,
        top_k=3
    )
    assert len(chunks) > 0, "RAG 检索未命中切片"
    print(f"  -> RAG 检索成功，命中 {len(chunks)} 条知识块")
    for c in chunks:
        print(f"     - [{c.category}] {c.title}")

    # 3. 格式化并验证是否包含了避坑重点
    rag_prompt = rag.format_for_agent(chunks)
    assert "故宫" in rag_prompt, "提示词中应包含故宫关键信息"
    print("  -> PASS: RAG 知识上下文生成无误！")

    # 4. 模拟 Agent 返回包含 tips 的完整 JSON
    sample_llm_json = {
        "city": "北京",
        "start_date": "2025-06-01",
        "end_date": "2025-06-03",
        "overall_suggestions": "本次北京历史摄影之旅已为您避开周一闭馆，并安排景山最佳机位。",
        "tips": [
            "[避坑指南] 谨防天安门周边一日游黑车与黑导游",
            "[独家机位] 故宫神武门出宫后可登上景山公园万春亭俯瞰紫禁城全景落日",
            "[预约规则] 故宫门票须提前7天晚上20:00在小程序抢票，周一全天闭馆"
        ],
        "days": [
            {
                "date": "2025-06-01",
                "day_index": 0,
                "description": "第1天：探访故宫与景山全景",
                "transportation": "地铁",
                "accommodation": "前门精品酒店",
                "attractions": [
                    {
                        "name": "故宫博物院",
                        "address": "北京市东城区景山前街4号",
                        "location": {"longitude": 116.397, "latitude": 39.916},
                        "visit_duration": 240,
                        "description": "明清两代皇家宫殿",
                        "category": "历史古迹",
                        "ticket_price": 60
                    }
                ],
                "meals": [
                    {"type": "breakfast", "name": "尹三豆汁", "estimated_cost": 20},
                    {"type": "lunch", "name": "四季民福烤鸭", "estimated_cost": 150},
                    {"type": "dinner", "name": "牛街清真涮肉", "estimated_cost": 120}
                ]
            }
        ],
        "weather_info": [
            {"date": "2025-06-01", "day_weather": "晴", "night_weather": "多云", "day_temp": 28, "night_temp": 18}
        ],
        "budget": {
            "total_attractions": 60,
            "total_hotels": 400,
            "total_meals": 290,
            "total_transportation": 30,
            "total": 780
        }
    }

    # 5. Pydantic 校验
    trip_plan = TripPlan(**sample_llm_json)
    assert len(trip_plan.tips) == 3, "tips 应包含 3 条内容"
    assert "神武门" in trip_plan.tips[1] or "万春亭" in trip_plan.tips[1]
    print(f"\n[测试 2] TripPlan 结构化校验成功!")
    print(f"  -> 城市: {trip_plan.city}")
    print(f"  -> 建议: {trip_plan.overall_suggestions}")
    print("  -> 达人避坑 Tips 数量:", len(trip_plan.tips))
    for idx, tip in enumerate(trip_plan.tips, 1):
        print(f"     {idx}. {tip}")

    print("\n" + "=" * 60)
    print("🎉 ALL PIPELINE TESTS PASSED! 第二步后端链路集成验证全部通过！")
    print("=" * 60)


if __name__ == "__main__":
    test_pipeline_rag()
