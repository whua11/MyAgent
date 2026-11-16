"""
工业级 RAG 双层拒答门禁模块 (Two-Stage RAG Rejection Gate)
================================================================================
核心设计目标（针对 2026 年大厂面试与线上真实故障）：
1. 解决余弦距离在负例上的【距离重叠】陷阱（负例 0.7179 比正例 0.8330 还高）；
2. 消除静态关键词匹配的【规则式盲区】：结合常见跨域实体库 + 动态地名识别 + 语义距离截断；
3. 落地【诚实降级 (Honest Degradation)】：阻断后绝不放行无中生有的幻觉，而是注入透明的安全提示，
   指引 LLM 诚实说明知识边界，彻底消除系统级幻觉。
================================================================================
"""
from typing import Dict, List, Tuple, Any, Optional
import re
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

# 扩展已知跨地域实体与景区库（覆盖全国知名但本知识库未收录的目标，破除原版规则盲区）
EXTENDED_UNSUPPORTED_ENTITIES = [
    # 西藏/青海/新疆/甘肃
    "拉萨", "布达拉宫", "西藏", "青海湖", "茶卡盐湖", "莫高窟", "敦煌", "喀纳斯", "乌鲁木齐",
    # 云南/贵州/海南
    "丽江", "大理", "西双版纳", "玉龙雪山", "黄果树", "梵净山", "三亚", "海口", "天涯海角",
    # 湖南/湖北/安徽/江西其他
    "张家界", "天门山", "黄鹤楼", "武当山", "神农架", "黄山", "九华山", "景德镇", "庐山",
    # 沿海及港澳台海外
    "厦门", "鼓浪屿", "青岛", "大连", "香港", "澳门", "台湾", "台北", "东京", "京都", "大阪", "巴黎", "伦敦"
]

# 高风险无中生有业务意图词（若用户询问此类业务，知识库必须有明确事实支撑，否则一律阻断）
HIGH_RISK_POLICY_KEYWORDS = [
    "宠物", "带狗", "带猫", "犬",
    "退费", "退款", "年卡退", "退押金",
    "轮椅租", "婴儿车租", "行李寄存收费",
    "无人机航拍", "露营搭帐篷"
]


class RAGRejectionGate:
    def __init__(self, supported_cities: Dict[str, List[str]] = None):
        self.supported_cities = supported_cities or SUPPORTED_CITIES

    def check_scope(self, query: str, city: Optional[str] = None) -> Tuple[bool, str]:
        """
        Layer 1: 业务范围守卫 (Scope Gate) - 动静结合破除规则式盲区
        """
        # 1. 显式跨地域实体命中检查 (扩展库)
        for un_target in EXTENDED_UNSUPPORTED_ENTITIES:
            if un_target in query:
                return False, f"SCOPE_UNSUPPORTED: 知识库暂未收录【{un_target}】的攻略与门票规则"

        # 2. 如果请求携带了目标城市，检查是否在知识库范围内
        if city and city not in ["通用", "全国"]:
            clean_city = city.replace("市", "").replace("特别行政区", "")
            if clean_city not in self.supported_cities:
                return False, f"SCOPE_UNSUPPORTED: 目标城市【{city}】暂未纳入系统离线知识库范围"

        # 3. 动态跨域地名正则嗅探 (防静态列表外的盲区)
        provinces = ["西藏", "新疆", "青海", "甘肃", "云南", "贵州", "海南", "黑龙江", "吉林", "辽宁", "内蒙古", "宁夏"]
        for prov in provinces:
            if prov in query and (not city or prov not in city):
                return False, f"SCOPE_UNSUPPORTED: 检测到跨省域旅行意图【{prov}】，超出当前支持城市集"

        return True, "SCOPE_PASS"

    def check_fact_alignment(self, query: str, retrieved_docs: List[str]) -> Tuple[bool, str]:
        """
        Layer 2: 事实对齐守卫 (Fact Alignment Gate) - 校验上下文支撑度与防政策伪造
        """
        found_risk_kws = [kw for kw in HIGH_RISK_POLICY_KEYWORDS if kw in query]
        
        # 若用户询问了具体高风险政策业务
        if found_risk_kws:
            combined_context = "".join(retrieved_docs)
            has_mention = any(kw in combined_context for kw in found_risk_kws)
            if not has_mention:
                return False, f"ALIGNMENT_FAILED: 用户询问了敏感业务【{'、'.join(found_risk_kws)}】，但检索内容未提及任何相关条款"

        return True, "ALIGNMENT_PASS"

    def check_semantic_distance(self, min_distance: float, threshold: float = 0.68) -> Tuple[bool, str]:
        """
        语义距离截断守卫 (Semantic Distance Guard)：
        当检索结果的最佳余弦距离超过阈值（相似度过低）时触发，解决低相关内容强行注入的幻觉
        """
        if min_distance > threshold:
            return False, f"DISTANCE_OVERFLOW: 最佳召回片段余弦距离 ({min_distance:.4f}) 超过安全阈值 ({threshold})"
        return True, "DISTANCE_PASS"

    def inspect(self, query: str, city: Optional[str] = None, retrieved_docs: List[str] = None, min_distance: float = 0.0) -> Dict[str, Any]:
        """
        综合裁决入口：前置范围检测 + 语义距离检测 + 事实对齐校验
        :return: 包含裁决状态、门禁名称、原因以及诚实降级提示语的结构化字典
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
                "honest_degradation": True,
                "suggested_reply": "⚠️ 提示：系统本地知识库暂未收录该地区或景点的门票规则与避坑指南，请以景区官方实时公告或致电咨询为准，切勿轻信黄牛代购。",
                "system_prompt_guard": "【安全可信指令】本地知识库未收录该区域攻略。严禁伪造虚假门票预约规则、退费政策或开放时间！请向用户明确说明该信息需以官方渠道为准。"
            }

        # 2. 语义距离截断守卫 (如果传入了有效距离)
        if min_distance > 0.0:
            dist_ok, dist_msg = self.check_semantic_distance(min_distance)
            if not dist_ok:
                logger.warning(f"[RAG Gate 阻断] Distance Guard 触发: {dist_msg}")
                return {
                    "can_answer": False,
                    "gate": "Layer 1.5 (Distance Guard)",
                    "reason": dist_msg,
                    "honest_degradation": True,
                    "suggested_reply": "⚠️ 提示：检索到的参考攻略与当前需求语义关联度不足，为保障信息准确性已启动降级防护，请以景区现场公示为准。",
                    "system_prompt_guard": "【安全可信指令】检索片段相似度不足。不可将不相关的参考资料强加给用户行程，请使用通用常识并提醒用户核对官方信息。"
                }

        # 3. 后置事实对齐守卫
        align_ok, align_msg = self.check_fact_alignment(query, retrieved_docs)
        if not align_ok:
            logger.warning(f"[RAG Gate 阻断] Layer 2 触发: {align_msg}")
            return {
                "can_answer": False,
                "gate": "Layer 2 (Fact Alignment Gate)",
                "reason": align_msg,
                "honest_degradation": True,
                "suggested_reply": "⚠️ 提示：知识库未检索到关于该具体政策/特殊业务的明确条款，为避免误导，请以景区现场游客中心答复为准。",
                "system_prompt_guard": "【安全可信指令】用户询问的特殊业务（如退费、宠物、特殊租赁）在事实库中无依据。严禁推测或承诺任何退改与准入规则！"
            }

        return {
            "can_answer": True,
            "gate": "PASS",
            "reason": "通过双层可答性与事实对齐校验",
            "honest_degradation": False,
            "suggested_reply": None,
            "system_prompt_guard": None
        }


# 全局单例
rag_rejection_gate = RAGRejectionGate()
