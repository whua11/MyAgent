"""
【学习注释版】旅行规划 API 路由

这个文件做什么？
- 对外提供「生成旅行计划」的 HTTP 接口
- 前端 Home.vue -> api.ts 会调用这里的 POST /api/trip/plan

谁注册/调用它？
- main.py 里：app.include_router(trip.router, prefix="/api")
- 所以本文件 router 的 prefix="/trip" + main 的 "/api"
  => 最终完整路径是：POST /api/trip/plan

读这个文件时抓住 3 件事：
1. 请求进来长什么样（TripRequest）
2. 业务真正交给谁做（get_trip_planner_agent().plan_trip）
3. 返回给前端长什么样（TripPlanResponse）
"""

import os
import asyncio
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
# TripRequest: 前端发来的旅行需求（城市、日期、偏好等）
# TripPlanResponse: 返回给前端的包装结构 {success, message, data}
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse  # 目前本文件未直接使用，保留作错误结构参考
)
# 拿到「多智能体旅行规划器」单例（原版与 LangGraph 版）
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...agents.langgraph_trip_planner import get_langgraph_trip_planner
from ...services.trace_service import trace_manager
from ...db.dao import trip_record_dao
from ...core.rate_limiter import llm_rate_limiter

# 创建一个路由分组：
# - prefix="/trip"：这个文件里的路径都会自动带 /trip
# - tags：只影响 /docs 文档里的分组显示
router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",  # 相对路径；完整 URL = /api + /trip + /plan
    response_model=TripPlanResponse,  # 告诉 FastAPI：返回值按这个模型序列化/校验
    summary="生成旅行计划",  # /docs 里显示的短标题
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(request: TripRequest):
    """
    生成旅行计划（项目最核心的后端入口）

    调用链：
    Home.vue handleSubmit
      -> api.ts generateTripPlan
      -> 本函数 plan_trip
      -> MultiAgentTripPlanner.plan_trip
      -> 返回 TripPlanResponse 给前端

    Args:
        request: FastAPI 会自动把 JSON body 解析并校验成 TripRequest
                 缺字段/类型不对时，这里都进不来，会直接 422

    Returns:
        TripPlanResponse: success/message/data(TripPlan)
    """
    try:
        # ---------- 1) 打印请求，方便你在后端终端观察联调 ----------
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        # ---------- 2) 引擎调度：支持双引擎热插拔 ----------
        # 默认使用 LangGraph 并发状态图引擎，可通过 .env 设置 USE_LANGGRAPH=false 切回原版
        use_langgraph = os.getenv("USE_LANGGRAPH", "true").lower() in ("true", "1", "yes")

        # 🛡️ P0 级后端工程修复：使用 asyncio.to_thread 将耗时 50~90s 的同步长任务分发至 worker 线程池
        # 彻底解决在 async def 中调用同步阻塞代码霸占 asyncio 事件循环的严重性能故障 (5.5s vs 2ms)
        if use_langgraph:
            print("⚡ [引擎调度] 启用【LangGraph 并发状态图智能体引擎 (asyncio.to_thread 异步分发)】")
            planner = get_langgraph_trip_planner()
            trip_plan = await asyncio.to_thread(planner.plan_trip, request, request.task_id)
        else:
            print("🤖 [引擎调度] 启用【原版 HelloAgents 线性流水线引擎 (asyncio.to_thread 异步分发)】")
            agent = get_trip_planner_agent()
            trip_plan = await asyncio.to_thread(agent.plan_trip, request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        # ---------- 4) 包装成前端约定的响应格式 ----------
        # 前端 api.ts 拿到后用的是 response.data（axios 层）
        # 再取业务里的 data 字段，即这里的 trip_plan
        trace_id = getattr(trip_plan, "trace_id", None)
        task_id = getattr(trip_plan, "task_id", getattr(request, "task_id", None))
        degraded = getattr(trip_plan, "degraded", False)
        degrade_reason = getattr(trip_plan, "degrade_reason", None)
        message = "旅行计划生成成功" if not degraded else f"已生成诚实降级方案: {degrade_reason}"
        return TripPlanResponse(
            success=True,
            message=message,
            data=trip_plan,
            trace_id=trace_id,
            task_id=task_id,
            degraded=degraded,
            degrade_reason=degrade_reason
        )

    except Exception as e:
        # 任意异常都转成 HTTP 500，前端 axios 会进 catch
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/plan/stream",
    summary="生成旅行计划 (SSE 流式推流)",
    description="基于 Server-Sent Events (SSE) 实时推送智能体各节点的执行状态与最终规划方案，并支持客户端掉线熔断"
)
async def plan_trip_stream(request: TripRequest, http_request: Request):
    """
    SSE 流式推流接口 (支持断点恢复、防 Nginx 缓冲截留与客户端掉线感知)：
    在推流过程中实时检测客户端连接状态，客户端断开立即熔断下游计算，杜绝僵尸任务
    """
    planner = get_langgraph_trip_planner()

    async def event_generator():
        try:
            for event in planner.plan_trip_stream(request):
                # 🛡️ 客户端掉线主动嗅探：如果用户关闭浏览器/断网，立即熔断下游生成
                if await http_request.is_disconnected():
                    print("🛑 [SSE 推流中止] 检测到客户端已主动断开连接 (is_disconnected)，熔断下游计算！")
                    break
                yield event
                await asyncio.sleep(0.01)  # 让出协程时间片给其他并发请求
        except asyncio.CancelledError:
            print("🛑 [SSE 任务取消] 接收到 CancelledError 信号，优雅终止生成")
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==============================================================
# 数据库持久化、B+树索引与限流观测接口
# ==============================================================

@router.get(
    "/records",
    summary="获取持久化行程记录列表 (带 B+ 树索引查询)",
    description="从 trip_records 业务表中查询历史行程，支持按城市与状态过滤"
)
async def list_trip_records(city: str = None, status: str = None, limit: int = 20):
    """从数据库持久化表中分页读取历史规划记录"""
    records = trip_record_dao.list_records(city=city, status=status, limit=limit)
    return {"success": True, "count": len(records), "data": records}


@router.get(
    "/explain-plan",
    summary="查看数据库执行计划 (EXPLAIN QUERY PLAN)",
    description="演示 SQLite/B+ 树联合索引最左前缀匹配与主键索引点查的执行计划分析"
)
async def explain_query_plan():
    """执行计划分析接口，直接查看联合索引与主键点查的底层执行步骤"""
    plans = trip_record_dao.explain_query_plans()
    return {"success": True, "data": plans}


@router.get(
    "/limiter-metrics",
    summary="获取并发限流与削峰熔断指标",
    description="查看当前系统的并发信号量占用数、总执行次数与 429 退避重试统计"
)
async def get_limiter_metrics():
    """查看当前并发信号量与重试器运行指标"""
    return {"success": True, "data": llm_rate_limiter.get_metrics()}


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """
    旅行规划子服务健康检查

    完整路径：GET /api/trip/health
    作用：确认 Agent 能否初始化，并返回各个 Agent 名称/工具数
    注意：这和 main.py 的 GET /health 不是同一个接口
    """
    try:
        # 如果 Agent 初始化失败（比如高德 Key 有问题），这里会抛错
        agent = get_trip_planner_agent()

        return {
            "status": "healthy",
            "service": "trip-planner",
            # 把 4 个专家 Agent 的名字暴露出来，便于调试确认系统就绪
            "agents": {
                "attraction": agent.attraction_agent.name,
                "weather": agent.weather_agent.name,
                "hotel": agent.hotel_agent.name,
                "planner": agent.planner_agent.name,
            },
            # 景点 Agent 挂了多少工具（MCP auto_expand 后通常是十几个）
            "tools_count": len(agent.attraction_agent.list_tools())
        }
    except Exception as e:
        # 503 = 服务暂时不可用
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )


# ==============================================================
# 链路追踪 (Tracing & Observability) 调试接口
# ==============================================================

@router.get(
    "/trace/latest",
    summary="获取最新链路 Trace 追踪详情",
    description="获取最近一次执行旅行规划的各智能体节点耗时、输入输出与执行状态"
)
async def get_latest_trace():
    """获取最近一次运行的 Trace 详细快照"""
    trace = trace_manager.get_latest_trace()
    if not trace:
        raise HTTPException(status_code=404, detail="暂无可用 Trace 记录")
    return {"success": True, "data": trace}


@router.get(
    "/trace/{trace_id}",
    summary="根据 Trace ID 查询调用链路详情",
    description="查询特定请求的完整执行链路、节点耗时与数据快照"
)
async def get_trace_by_id(trace_id: str):
    """根据 Trace ID 查询特定调用链路详情"""
    trace = trace_manager.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"未找到 Trace ID 为 {trace_id} 的追踪记录")
    return {"success": True, "data": trace}


@router.get(
    "/traces",
    summary="获取历史 Trace 列表",
    description="获取最近历史调用的 Trace 列表摘要"
)
async def list_traces(limit: int = 20):
    """获取历史 Trace 列表"""
    traces = trace_manager.list_traces(limit=limit)
    return {"success": True, "count": len(traces), "data": traces}
