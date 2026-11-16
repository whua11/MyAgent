"""
行程规划持久化数据模型 (Trip Planning Database Models)
包含严谨的索引设计：
- 主键索引: task_id (B+ 树聚簇索引)
- 单列索引: status, city
- 联合索引: idx_city_created (city, created_at) -> 支撑高频按城市分页查询，满足最左前缀匹配
"""
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
    Index
)
from .database import Base


class TripRecord(Base):
    """
    旅行规划业务记录表 (持久化用户任务执行状态与完整行程)
    与 LangGraph checkpointing 互补：checkpoint 存运行时节点快照，本表存结构化业务实体
    """
    __tablename__ = "trip_records"

    # 1. 主键与任务幂等键 (B+ 树聚簇索引)
    task_id = Column(String(64), primary_key=True, index=True, comment="全局唯一任务幂等标识")

    # 2. 查询条件列 (带索引)
    city = Column(String(32), nullable=False, index=True, comment="目的地城市")
    start_date = Column(String(16), nullable=True, comment="出行开始日期")
    end_date = Column(String(16), nullable=True, comment="出行结束日期")
    travel_days = Column(Integer, default=3, comment="出行天数")
    transportation = Column(String(32), nullable=True, comment="偏好交通方式")
    accommodation = Column(String(32), nullable=True, comment="偏好住宿类型")

    # 3. 产出数据与财务指标
    budget_total = Column(Float, default=0.0, comment="预算总计 (元)")
    plan_json = Column(Text, nullable=True, comment="完整行程 JSON 序列化字符串")

    # 4. 任务生命周期状态 (PENDING -> RUNNING -> COMPLETED / FAILED)
    status = Column(String(16), default="PENDING", index=True, nullable=False, comment="任务状态")
    error_message = Column(Text, nullable=True, comment="异常失败原因")
    trace_id = Column(String(64), nullable=True, index=True, comment="分布式链路追踪 ID")

    # 5. 时间审计字段
    created_at = Column(DateTime, default=datetime.utcnow, index=True, comment="任务创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="最近更新时间")

    # 6. 复合索引：按城市 + 创建时间倒序检索，常用于目的地看板与热门路线查询
    __table_args__ = (
        Index("idx_city_created", "city", "created_at"),
        Index("idx_status_created", "status", "created_at"),
    )

    def to_dict(self):
        """将 ORM 对象转换为标准 dict 字典"""
        return {
            "task_id": self.task_id,
            "city": self.city,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "travel_days": self.travel_days,
            "transportation": self.transportation,
            "accommodation": self.accommodation,
            "budget_total": self.budget_total,
            "status": self.status,
            "trace_id": self.trace_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "has_plan": bool(self.plan_json),
            "error_message": self.error_message
        }
