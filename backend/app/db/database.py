"""
数据库连接与会话管理 (Database Connection & Session Factory)
基于 SQLite + SQLAlchemy 2.0 构建持久化业务库，存放行程历史与状态机记录。
用于支撑高可靠后端设计：
1. 任务状态追踪 (PENDING -> RUNNING -> COMPLETED / FAILED)
2. B+ 树索引与联合索引最左前缀测试 (city, created_at)
3. 执行计划测试 (EXPLAIN QUERY PLAN)
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DB_DIR = BACKEND_DIR / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "trip_records.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False 允许在多线程 (FastAPI 线程池) 中复用连接
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入会话生成器 (Dependency Injection)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表结构与索引"""
    from . import models
    Base.metadata.create_all(bind=engine)
