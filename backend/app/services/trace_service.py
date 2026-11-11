"""
【链路追踪与可观测性模块】Local Trace Inspector
为 LangGraph 多智能体系统提供全流程调用链路追踪、节点耗时监控、输入/输出快照与可视化报告。
"""

import os
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger

# Trace 存储目录：backend/logs/traces/
TRACE_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)


class TraceManager:
    """本地轻量级链路追踪管理器"""

    def __init__(self):
        self._latest_trace_id: Optional[str] = None

    def create_trace_id(self) -> str:
        """生成全局唯一 Trace ID：trace_年月日_时分秒_6位随机串"""
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        rand_str = uuid.uuid4().hex[:6]
        trace_id = f"trace_{now_str}_{rand_str}"
        self._latest_trace_id = trace_id
        return trace_id

    def build_record(
        self,
        node_name: str,
        duration_ms: float,
        status: str = "SUCCESS",
        summary: str = "",
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """构建单个节点的 Trace 记录项"""
        return {
            "node": node_name,
            "duration_ms": round(duration_ms, 2),
            "status": status,
            "summary": summary,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "inputs": inputs or {},
            "outputs": outputs or {},
            "error": error
        }

    def finalize_trace(
        self,
        trace_id: str,
        request: Any,
        total_duration_ms: float,
        plan: Any,
        records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """归档并保存完整 Trace，打印控制台瀑布流报告"""
        req_dict = request.model_dump() if hasattr(request, "model_dump") else (request if isinstance(request, dict) else {})
        plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else (plan if isinstance(plan, dict) else {})

        # 按时间戳排序节点
        sorted_records = sorted(records, key=lambda x: x.get("timestamp", ""))

        trace_data = {
            "trace_id": trace_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_duration_ms": round(total_duration_ms, 2),
            "status": "SUCCESS" if plan else "FAILED",
            "request": req_dict,
            "nodes_count": len(sorted_records),
            "nodes": sorted_records,
            "plan_summary": {
                "city": plan_dict.get("city"),
                "days_count": len(plan_dict.get("days", [])),
                "hotels": [d.get("hotel", {}).get("name") for d in plan_dict.get("days", []) if d.get("hotel")],
                "total_budget": plan_dict.get("budget", {}).get("total") if plan_dict.get("budget") else 0
            }
        }

        # 1. 保存至磁盘 JSON 文件
        file_path = TRACE_DIR / f"{trace_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(trace_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 Trace 文件失败: {e}")

        self._latest_trace_id = trace_id

        # 2. 控制台格式化打印瀑布流看板
        self._print_waterfall(trace_data)

        return trace_data

    def _print_waterfall(self, trace_data: Dict[str, Any]):
        """在控制台打印精美的 ASCII 链路耗时看板"""
        tid = trace_data["trace_id"]
        total_ms = trace_data["total_duration_ms"]
        nodes = trace_data.get("nodes", [])

        bar = "=" * 80
        subbar = "-" * 80
        lines = [
            f"\n{bar}",
            f"📊 【LangGraph 链路 Trace 耗时分析】 [{tid}]",
            bar
        ]

        node_names = {
            "intent_rewrite_node": "🎯 意图改写 (Intent Rewrite)",
            "weather_node":        "⛅ 天气查询 (Weather POI)",
            "attraction_node":     "🏛️ 景点匹配 (Attraction POI)",
            "hotel_node":          "🏨 住宿筛选 (Hotel POI)",
            "food_node":           "🍜 美食挖掘 (Food POI)",
            "rag_node":            "📚 本地向量库 (RAG Search)",
            "planner_node":        "🤖 大模型编制 (Planner LLM)",
            "validator_node":      "🔍 规范质检 (Validator)"
        }

        for i, n in enumerate(nodes, 1):
            name = node_names.get(n["node"], n["node"])
            ms = n["duration_ms"]
            status = "✅" if n["status"] == "SUCCESS" else "❌"
            summary = n.get("summary", "")
            lines.append(f"{i:2d}. {status} {name:<26} 耗时: {ms:8.1f}ms | {summary}")

        lines.append(subbar)
        lines.append(f"⏱️ 链路总耗时: {total_ms:.1f}ms | 执行节点数: {len(nodes)} | 结果: {trace_data['status']}")
        lines.append(f"💾 完整 JSON 快照: backend/logs/traces/{tid}.json")
        lines.append(f"{bar}\n")

        print("\n".join(lines))

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """读取指定 trace_id 的 JSON 数据"""
        file_path = TRACE_DIR / f"{trace_id}.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取 Trace 文件异常: {e}")
            return None

    def get_latest_trace(self) -> Optional[Dict[str, Any]]:
        """获取最近一次运行的 Trace"""
        if self._latest_trace_id:
            res = self.get_trace(self._latest_trace_id)
            if res:
                return res

        files = list(TRACE_DIR.glob("trace_*.json"))
        if not files:
            return None
        latest_file = max(files, key=lambda f: f.stat().st_mtime)
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def list_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出最近的 Trace 摘要列表"""
        files = list(TRACE_DIR.glob("trace_*.json"))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        results = []
        for f in files[:limit]:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                    results.append({
                        "trace_id": d.get("trace_id"),
                        "created_at": d.get("created_at"),
                        "total_duration_ms": d.get("total_duration_ms"),
                        "status": d.get("status"),
                        "city": d.get("request", {}).get("city"),
                        "nodes_count": d.get("nodes_count")
                    })
            except Exception:
                continue
        return results


# 全局单例
trace_manager = TraceManager()

def get_trace_manager() -> TraceManager:
    return trace_manager
