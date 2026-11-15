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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
# TripRequest: 前端发来的旅行需求（城市、日期、偏好等）
# TripPlanResponse: 返回给前端的包装结构 {success, message, data}
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse  # 目前本文件未直接使用，保留作错误结构参考
)
# 拿到「多智能体旅行规划器」单例（原版与 LangGraph 版）
import os
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...agents.langgraph_trip_planner import get_langgraph_trip_planner
from ...services.trace_service import trace_manager

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

        if use_langgraph:
            print("⚡ [引擎调度] 启用【LangGraph 并发状态图智能体引擎】")
            planner = get_langgraph_trip_planner()
            trip_plan = planner.plan_trip(request)
        else:
            print("🤖 [引擎调度] 启用【原版 HelloAgents 线性流水线引擎】")
            agent = get_trip_planner_agent()
            trip_plan = agent.plan_trip(request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        # ---------- 4) 包装成前端约定的响应格式 ----------
        # 前端 api.ts 拿到后用的是 response.data（axios 层）
        # 再取业务里的 data 字段，即这里的 trip_plan
        trace_id = getattr(trip_plan, "trace_id", None)
        task_id = getattr(trip_plan, "task_id", getattr(request, "task_id", None))
        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            trace_id=trace_id,
            task_id=task_id
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
    description="基于 Server-Sent Events (SSE) 实时推送智能体各节点的执行状态与最终规划方案"
)
async def plan_trip_stream(request: TripRequest):
    """
    SSE 流式推流接口 (支持断点恢复与防 Nginx 缓冲截留)：
    让前端实时接收 Agent 节点执行事件，避免白屏等待，大幅优化首字体验
    """
    planner = get_langgraph_trip_planner()
    return StreamingResponse(
        planner.plan_trip_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


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
