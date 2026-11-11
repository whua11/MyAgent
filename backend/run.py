"""
【学习注释版】后端启动脚本

用法（在 backend 目录）：
    python run.py

它做什么？
- 用 uvicorn 启动 FastAPI 应用：app.api.main:app
- Windows 下强制 stdout/stderr 用 UTF-8，避免打印 emoji 时 GBK 崩溃

和 main.py 的关系：
- main.py 定义 app
- run.py 负责“把 app 跑起来”
"""

import os
import sys

import uvicorn
from app.config import get_settings

# Windows 控制台默认常是 GBK；项目日志里有 emoji，不处理会启动失败
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

if __name__ == "__main__":
    # 只有直接运行本文件时才启动服务
    # （被别人 import 时不会误启动）
    settings = get_settings()

    uvicorn.run(
        "app.api.main:app",          # 指向 FastAPI 实例
        host=settings.host,          # 通常 0.0.0.0，局域网可访问
        port=settings.port,          # 通常 8000
        reload=True,                 # 开发模式热重载
        log_level=settings.log_level.lower()
    )
