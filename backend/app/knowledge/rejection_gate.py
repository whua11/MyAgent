"""
工业级 RAG 双层拒答门禁模块 (Two-Stage RAG Rejection Gate)
========================================================================
设计动机：
实测发现余弦距离对不可答问题（如跨城市旅游、未收录政策业务）容易出现虚假高分（距离 0.7179 比合法问题的 0.8330 还小）。
纯距离阈值必然引发"两难困境"：卡严了误伤合法问题，卡松了放行伪造幻觉。
因此构建显式双层阻断闸门：
  • Layer 1: 业务范围与地域实体守卫 (Scope Gate)
  • Layer 2: 核心实体与检索上下文事实对齐校验 (Fact Alignment Gate)
========================================================================
"""
from typing import Dict, List, Tuple, Any
from loguru import logger

# 知识库当前明确支持的城市名录与主要别称
SUPPORTED_CITIES = {
    "北京": ["北京", "京城", "紫禁城", "故宫"],
    "成都": ["成都", "蓉城", "锦官城"],
    "重庆": ["重庆", "山城", "渝中"],
    "杭州": ["杭州", "钱塘", "西湖"],
    "南昌": ["南昌", "洪城", "豫章"],
    "上海": ["上海", "申城", "魔都"],
    "西安": ["西安", "长安"]
}

# 明确已知的常见跨地域/超范围地名列表
KNOWN_UNSUPPORTED_REGIONS = [
    "拉萨", "布达拉宫", "西藏", "三亚", "海口", "厦门", "青岛", "大连",
    "香港", "澳门", "台湾", "台北", "东京", "京都", "大阪", "巴黎", "伦敦", "纽约"
]

# 高风险无中生有业务意图词（若用户询问此类业务，知识库必须有明确依据，否则必须阻断）
HIGH_RISK_POLICY_KEYWORDS = [
    "宠物", "带狗", "带猫", "犬",
    "退费", "退款", "年卡退", "退押金",
    "轮椅租", "婴儿车租", "行李寄存收费",
    "无人机航拍", "露营搭帐篷"
]


class RAGRejectionGate:
    def __init__(self, supported_cities: Dict[str, List[str]] = None):
        self.supported_cities = supported_cities or SUPPORTED_CITIES

    def check_scope(self, query: str, city: str = None) -> Tuple[bool, str]:
        """
        Layer 1: 知识库服务范围与超纲地域实体前置拦截
        """
        # 1. 检测是否包含明确不受支持的跨地域实体
        for un_target in KNOWN_UNSUPPORTED_REGIONS:
            if un_target in query:
                return False, f"SCOPE_UNSUPPORTED: 知识库暂未收录【{un_target}】的攻略与门票规则"

        # 2. 如果请求携带了目标城市，检查是否在名录内
        if city and city not in ["通用", "全国"]:
            clean_city = city.replace("市", "").replace("特别行政区", "")
            if clean_city not in self.supported_cities:
                return False, f"SCOPE_UNSUPPORTED: 目标城市【{city}】暂未纳入系统知识库范围"

        return True, "SCOPE_PASS"

    def check_fact_alignment(self, query: str, retrieved_docs: List[str]) -> Tuple[bool, str]:
        """
        Layer 2: 核心实体与上下文事实对齐校验 (防张冠李戴与伪造政策)
        """
        found_risk_kws = [kw for kw in HIGH_RISK_POLICY_KEYWORDS if kw in query]
        
        # 若用户询问了具体高风险政策业务
        if found_risk_kws:
            combined_context = "".join(retrieved_docs)
            # 校验检索上文里是否真实包含相关词汇
            has_mention = any(kw in combined_context for kw in found_risk_kws)
            if not has_mention:
                return False, f"ALIGNMENT_FAILED: 用户询问了【{'、'.join(found_risk_kws)}】，但检索内容未提及任何相关规定"

        return True, "ALIGNMENT_PASS"

    def inspect(self, query: str, city: str = None, retrieved_docs: List[str] = None) -> Dict[str, Any]:
        """
        综合裁决入口：在检索前后执行两道门禁
        :return: {"can_answer": bool, "gate": str, "reason": str}
        """
        retrieved_docs = retrieved_docs or []
        
        # 1. 前置范围守卫
        scope_ok, scope_msg = self.check_scope(query, city)
        if not scope_ok:
            logger.warning(f"[RAG Gate 阻断] Layer 1 触发: {scope_msg}")
            return {
                "can_answer": False,
                "gate": "Layer 1 (Scope Gate)",
                "reason": scope_msg,
                "suggested_reply": "抱歉，知识库暂未收录该地区或景点的门票规则与攻略，建议查询官方官方公告或致电景区咨询。"
            }

        # 2. 后置事实对齐守卫
        align_ok, align_msg = self.check_fact_alignment(query, retrieved_docs)
        if not align_ok:
            logger.warning(f"[RAG Gate 阻断] Layer 2 触发: {align_msg}")
            return {
                "can_answer": False,
                "gate": "Layer 2 (Fact Alignment Gate)",
                "reason": align_msg,
                "suggested_reply": "抱歉，当前知识库中未包含关于该具体政策/特殊业务的明确条款，为避免提供错误信息，请以景区现场公告为准。"
            }

        return {
            "can_answer": True,
            "gate": "PASS",
            "reason": "通过双层可答性校验",
            "suggested_reply": None
        }


# 全局单例
rag_rejection_gate = RAGRejectionGate()
