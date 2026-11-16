"""
数据访问对象 (Data Access Object - DAO)
封装对 trip_records 表的增删改查、状态流转与执行计划 (EXPLAIN) 分析
"""
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import text, desc
from .database import SessionLocal, init_db
from .models import TripRecord

# 确保表结构在模块导入时即完成初始化
init_db()


class TripRecordDAO:
    @staticmethod
    def get_by_task_id(task_id: str) -> Optional[TripRecord]:
        """根据 task_id 查单条记录"""
        with SessionLocal() as db:
            return db.query(TripRecord).filter(TripRecord.task_id == task_id).first()

    @staticmethod
    def create_or_mark_running(
        task_id: str,
        city: str,
        start_date: str = None,
        end_date: str = None,
        travel_days: int = 3,
        transportation: str = None,
        accommodation: str = None,
        trace_id: str = None
    ) -> TripRecord:
        """任务启动时调用：如果记录不存在则创建，如果存在则更新为 RUNNING"""
        with SessionLocal() as db:
            record = db.query(TripRecord).filter(TripRecord.task_id == task_id).first()
            if not record:
                record = TripRecord(
                    task_id=task_id,
                    city=city,
                    start_date=start_date,
                    end_date=end_date,
                    travel_days=travel_days,
                    transportation=transportation,
                    accommodation=accommodation,
                    status="RUNNING",
                    trace_id=trace_id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(record)
            else:
                record.status = "RUNNING"
                record.trace_id = trace_id or record.trace_id
                record.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(record)
            return record

    @staticmethod
    def save_completed_plan(task_id: str, plan_dict: dict, budget_total: float = 0.0) -> Optional[TripRecord]:
        """任务成功完成时保存：写入完整 JSON 与状态 COMPLETED"""
        with SessionLocal() as db:
            record = db.query(TripRecord).filter(TripRecord.task_id == task_id).first()
            if record:
                record.status = "COMPLETED"
                record.budget_total = budget_total
                record.plan_json = json.dumps(plan_dict, ensure_ascii=False)
                record.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(record)
            return record

    @staticmethod
    def mark_failed(task_id: str, error_msg: str) -> Optional[TripRecord]:
        """任务异常失败时调用"""
        with SessionLocal() as db:
            record = db.query(TripRecord).filter(TripRecord.task_id == task_id).first()
            if record:
                record.status = "FAILED"
                record.error_message = error_msg
                record.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(record)
            return record

    @staticmethod
    def list_records(city: Optional[str] = None, status: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """按城市或状态分页查询历史规划"""
        with SessionLocal() as db:
            q = db.query(TripRecord)
            if city:
                q = q.filter(TripRecord.city == city)
            if status:
                q = q.filter(TripRecord.status == status)
            q = q.order_by(desc(TripRecord.created_at)).limit(limit)
            return [r.to_dict() for r in q.all()]

    @staticmethod
    def explain_query_plans() -> Dict[str, Any]:
        """
        执行计划分析 (EXPLAIN QUERY PLAN) - 面试硬核展示工具
        演示两组对比：
        1. 命中联合索引最左前缀 (city, created_at) 的高效检索 (SEARCH using index)
        2. 跨过最左前缀只查 created_at 导致的索引失效或回表扫描 (SCAN table)
        """
        with SessionLocal() as db:
            # 案例 1: 走联合索引最左前缀 idx_city_created
            sql_leftmost = "EXPLAIN QUERY PLAN SELECT task_id, city, budget_total FROM trip_records WHERE city = '北京' ORDER BY created_at DESC"
            res_leftmost = db.execute(text(sql_leftmost)).fetchall()
            leftmost_plan = [dict(row._mapping) for row in res_leftmost]

            # 案例 2: 单独查 task_id (主键聚簇索引等值查找)
            sql_pk = "EXPLAIN QUERY PLAN SELECT * FROM trip_records WHERE task_id = 'test_123'"
            res_pk = db.execute(text(sql_pk)).fetchall()
            pk_plan = [dict(row._mapping) for row in res_pk]

            return {
                "composite_index_search": {
                    "sql": sql_leftmost,
                    "plan": leftmost_plan,
                    "explanation": "命中联合索引 idx_city_created 的最左列 (city)，B+ 树通过索引快速定位并按时间倒序"
                },
                "pk_index_search": {
                    "sql": sql_pk,
                    "plan": pk_plan,
                    "explanation": "命中主键索引 sqlite_autoindex_trip_records_1，点查复杂度 O(log N)"
                }
            }


trip_record_dao = TripRecordDAO()
