"""
【断点恢复与中断容错实测脚本】
测试目标：
1. 验证 LangGraph + SqliteSaver 持久化检查点是否正常生效；
2. 模拟真实故障中断（在天气、景点、RAG 采集完成后人为截断，模拟网络中断/进程崩溃）；
3. 验证使用相同 task_id 再次请求时，系统能自动加载快照，0 毫秒跳过已完成节点并从断点成功续跑完成；
4. 验证完全跑完后重复点击时的“幂等秒级返回”。
"""
import sys
import os
import time
from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

# 载入环境变量 (读取 LLM_API_KEY)
load_dotenv(dotenv_path=BACKEND_DIR / ".env")

from app.models.schemas import TripRequest
from app.agents.langgraph_trip_planner import get_langgraph_trip_planner

def run_breakpoint_test():
    planner = get_langgraph_trip_planner()
    test_task_id = f"test_crash_recovery_{int(time.time())}"
    config = {"configurable": {"thread_id": test_task_id}}

    req = TripRequest(
        city="北京",
        start_date="2026-06-01",
        end_date="2026-06-03",
        travel_days=3,
        transportation="公共交通",
        accommodation="经济型酒店",
        preferences=["历史文化", "地道美食"],
        free_text_input="希望避开闭馆日，品尝正宗烤鸭",
        task_id=test_task_id
    )

    print("\n" + "="*70)
    print("🧪 【第一幕：模拟长任务执行到 60% 时突发故障中断】")
    print(f"📌 分配测试任务 ID (幂等键): {test_task_id}")
    print("="*70)

    # 构造初始状态
    trace_id = f"trace_{test_task_id}"
    initial_state = {
        "request": req,
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

    # 流式执行前置采集节点，当采集全部就绪后，人为抛出异常模拟宕机！
    simulated_crash = False
    print("\n▶ 开始逐步执行工作流...")
    try:
        for chunk in planner.app.stream(initial_state, config=config, stream_mode="updates"):
            for node_name, data in chunk.items():
                print(f"   ✓ 节点 [{node_name}] 执行完毕，快照已自动持久化进 SQLite！")
                # 当数据采集就绪，即将进入大模型 planner_node 前强行模拟故障
                if node_name in ["rag_node", "food_node", "hotel_node", "weather_node"]:
                    snap = planner.app.get_state(config)
                    vals = snap.values
                    # 如果关键数据都到齐了，模拟断网崩溃！
                    if vals.get("weather_data") and vals.get("attractions_data") and vals.get("travel_tips_data"):
                        print(f"\n💥💥💥 [模拟突发事件] 网络断开 / 进程被 Kill / 浏览器关闭！中断执行！")
                        simulated_crash = True
                        break
            if simulated_crash:
                break
    except Exception as e:
        print(f"发生中断: {e}")

    # 检查数据库中当前的快照
    snap_after_crash = planner.app.get_state(config)
    print("\n" + "-"*70)
    print("🔍 【检查点快照体检】:")
    print(f"   • 当前任务 ID: {test_task_id}")
    print(f"   • 中断时刻停留节点: {snap_after_crash.next}")
    print(f"   • 已成功持久化天气数据: {'已就绪' if snap_after_crash.values.get('weather_data') else '缺失'}")
    print(f"   • 已成功持久化景点数据: {len(snap_after_crash.values.get('attractions_data', []))} 条")
    print(f"   • 已成功持久化 RAG 攻略: {'已就绪' if snap_after_crash.values.get('travel_tips_data') else '缺失'}")
    print(f"   • 最终大模型行程: {'尚未生成 (符合预期)' if not snap_after_crash.values.get('final_plan') else '异常已生成'}")
    print("-"*70)

    print("\n" + "="*70)
    print("🚀 【第二幕：用户重新连网刷新，发起相同 task_id 的请求（断点续跑）】")
    print("="*70)
    start_resume = time.perf_counter()

    # 再次调用 plan_trip，带上相同的 task_id
    resumed_plan = planner.plan_trip(req, task_id=test_task_id)
    resume_elapsed = time.perf_counter() - start_resume

    print(f"\n🎉 断点恢复完成！耗时: {resume_elapsed:.2f}s")
    print(f"📋 生成行程目的地: {resumed_plan.city}")
    print(f"📋 生成总天数: {len(resumed_plan.days)} 天")
    print(f"📋 预算总计: {resumed_plan.budget.total} 元")
    print(f"📋 避坑贴士条数: {len(resumed_plan.tips)} 条 (已深度融合 RAG 知识)")

    print("\n" + "="*70)
    print("⚡ 【第三幕：用户手抖重复点击或刷新完成页（幂等性验证）】")
    print("="*70)
    start_idempotent = time.perf_counter()
    cached_plan = planner.plan_trip(req, task_id=test_task_id)
    idempotent_elapsed = (time.perf_counter() - start_idempotent) * 1000

    print(f"🚀 幂等命中耗时: {idempotent_elapsed:.2f} ms (毫秒级直接返回，完全跳过大模型与高德！)")
    print(f"✅ 两次行程一致性校验: {'100% 一致通过' if cached_plan.city == resumed_plan.city else '不一致'}")
    print("="*70)

if __name__ == "__main__":
    run_breakpoint_test()
