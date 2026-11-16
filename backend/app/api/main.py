"""
【学习注释版】FastAPI 主应用入口

这个文件做什么？
- 创建整个后端 Web 应用 app
- 配置跨域 CORS（否则前端 localhost:5173 调不了后端）
- 把 trip/poi/map 等路由挂到 /api 下
- 启动时校验 .env 配置

谁启动它？
- 推荐：backend/run.py  -> uvicorn.run("app.api.main:app", ...)
- 或直接：uvicorn app.api.main:app --reload

前端怎么连到这里？
- frontend/.env 的 VITE_API_BASE_URL=http://localhost:8000
- api.ts 发 POST /api/trip/plan，最终打到本应用挂载的 trip 路由
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ..config import get_settings, validate_config, print_config
from ..db.database import init_db
# trip: 旅行规划；poi: 景点图片/详情；map_routes: 地图相关接口
from .routes import trip, poi, map as map_routes

# 读取全局配置（host/port/cors/amap_api_key 等）
settings = get_settings()

# 创建 FastAPI 应用实例
# docs_url="/docs" => 打开 http://localhost:8000/docs 可手动调试接口
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="基于HelloAgents框架的智能旅行规划助手API",
    docs_url="/docs",
    redoc_url="/redoc"
)

# --------------- CORS 跨域中间件 ---------------
# 浏览器从 http://localhost:5173 访问 http://localhost:8000 属于跨域
# 不配 CORS，前端请求会被浏览器拦截
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),  # 允许的前端来源列表
    allow_credentials=True,
    allow_methods=["*"],   # 允许 GET/POST/...
    allow_headers=["*"],   # 允许自定义请求头
)

# --------------- 注册业务路由 ---------------
# trip.router 自身 prefix="/trip"
# 这里再加 prefix="/api" => /api/trip/plan
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")      # 如 /api/poi/photo
app.include_router(map_routes.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """
    应用启动钩子：服务一启动就执行一次

    完成的事情：
    1. 打印当前配置（隐藏敏感信息）
    2. 校验必要配置（尤其是 AMAP_API_KEY）
    3. 配置不对就直接启动失败，避免带病运行
    """
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)

    # 打印配置（Key 只显示“已配置/未配置”）
    print_config()

    # 验证配置：高德 Key 缺失会 raise ValueError
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查.env文件并确保所有必要的配置项都已设置")
        raise

    # 初始化数据库表结构与 B+ 树索引
    init_db()
    print("💾 业务数据库表结构与索引初始化完成")

    print("\n" + "="*60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("="*60 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭钩子：进程退出前打印提示（便于观察热重载/手动停止）"""
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


@app.get("/")
async def root():
    """
    根路径探活
    访问：GET http://localhost:8000/
    作用：快速确认服务是否起来了
    """
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health():
    """
    全局健康检查
    访问：GET http://localhost:8000/health
    前端 api.ts 的 healthCheck() 会调这个
    """
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


if __name__ == "__main__":
    # 允许 python -m / 直接运行本文件启动（更推荐用 run.py）
    import uvicorn

    uvicorn.run(
        "app.api.main:app",   # 导入路径：模块 app.api.main 里的 app 对象
        host=settings.host,
        port=settings.port,
        reload=True           # 改代码自动重启（开发用）
    )
