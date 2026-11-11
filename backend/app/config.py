"""
【学习注释版】配置管理模块

这个文件做什么？
- 从 .env 读取配置
- 提供全局 settings 对象
- 启动时校验必要配置（尤其是高德 Key）

谁在用？
- main.py：启动打印/校验配置、CORS origins
- trip_planner_agent.py：取 amap_api_key 传给 MCP
- llm_service / unsplash 等服务：读各自 Key
"""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 智能加载 backend/.env 无论当前工作目录在根目录还是 backend
backend_env = Path(__file__).resolve().parent.parent / ".env"
if backend_env.exists():
    load_dotenv(backend_env, override=False)
else:
    load_dotenv()

# 兼容教程目录结构：如果上级有 HelloAgents/.env，也尝试加载
# override=False：不覆盖已经存在的环境变量
helloagents_env = Path(__file__).resolve().parent.parent.parent.parent / "HelloAgents" / ".env"
if helloagents_env.exists():
    load_dotenv(helloagents_env, override=False)


class Settings(BaseSettings):
    """
    应用配置类
    pydantic-settings 会自动把同名环境变量映射到字段：
    - AMAP_API_KEY -> amap_api_key
    - HOST -> host
    等（不区分大小写）
    """

    app_name: str = "HelloAgents智能旅行助手"
    app_version: str = "1.0.0"
    debug: bool = False

    host: str = "0.0.0.0"   # 监听地址
    port: int = 8000        # 端口

    # CORS：逗号分隔字符串，用时再 split
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 高德 Web 服务 Key（给后端 MCP 用）
    amap_api_key: str = ""

    # Unsplash（景点配图，可选）
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # 下面 openai_* 是兼容字段；本项目实际更常用 LLM_* 环境变量
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4"

    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # .env 里多出来的变量（如 LLM_API_KEY）不报错

    def get_cors_origins_list(self) -> List[str]:
        """把 CORS 字符串切成列表，给 FastAPI CORSMiddleware 用"""
        return [origin.strip() for origin in self.cors_origins.split(',')]


# 全局单例配置
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例（全项目统一入口）"""
    return settings


def validate_config():
    """
    验证配置是否完整
    调用位置：main.py startup_event

    - 高德 Key 缺失：直接报错，阻止启动
    - LLM Key 缺失：只警告（因为也可能用别的变量名）
    """
    errors = []
    warnings = []

    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    # HelloAgentsLLM 读的是 LLM_API_KEY / OPENAI_API_KEY
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_api_key:
        warnings.append("LLM_API_KEY或OPENAI_API_KEY未配置,LLM功能可能无法使用")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


def print_config():
    """打印当前配置（隐藏敏感信息），方便启动时自检"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")

    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
    llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"日志级别: {settings.log_level}")
