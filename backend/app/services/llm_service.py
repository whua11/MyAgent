"""
【学习注释版】LLM 服务封装

这个文件做什么？
- 提供全局唯一的 HelloAgentsLLM 实例（单例）
- 让所有 Agent 共用同一个大模型客户端

谁调用？
- trip_planner_agent.py：self.llm = get_llm()

配置从哪来？
- HelloAgentsLLM() 会自动读环境变量：
  LLM_API_KEY / LLM_MODEL_ID / LLM_BASE_URL 等
- 这些变量写在 backend/.env
"""

from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 模块级缓存：进程内只创建一次
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取 LLM 实例（单例模式）

    为什么用单例？
    - 避免每次请求都重新初始化客户端
    - 多 Agent 共享同一个 llm 对象

    Returns:
        HelloAgentsLLM 实例
    """
    global _llm_instance

    if _llm_instance is None:
        # settings 这里主要是保持与配置模块的依赖关系；
        # 真正读 Key/模型的是 HelloAgentsLLM 内部的环境变量逻辑
        settings = get_settings()

        # 不传参：框架自动从 .env / 环境变量加载
        _llm_instance = HelloAgentsLLM()

        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")  # 如 deepseek
        print(f"   模型: {_llm_instance.model}")        # 如 deepseek-v4-flash

    return _llm_instance


def reset_llm():
    """
    重置 LLM 实例
    场景：改完 .env 想重新加载配置时（测试用）
    """
    global _llm_instance
    _llm_instance = None
