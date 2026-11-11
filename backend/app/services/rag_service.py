"""
【RAG 检索增强服务模块】

这个文件做什么？
1. 从 `backend/data/knowledge/` 加载各地旅行攻略与避坑知识库文件（Markdown格式）
2. 将长文解析切块为具备语义元数据（城市、分类、标签）的 KnowledgeChunk
3. 提供混合检索接口（城市精确过滤 + 用户偏好标签加权 + 关键词语义相关度）
4. 将检索出的知识格式化为注入给 Planner Agent 的高质量上下文

谁在用？
- trip_planner_agent.py：在 plan_trip 流水线中检索目标城市的深度攻略与避坑提示，注入大模型提示词
- 测试脚本 test_rag_service.py：用于验证检索准确度与召回率
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class KnowledgeChunk(BaseModel):
    """单条知识切片数据结构"""
    id: str = Field(..., description="切片唯一ID")
    city: str = Field(..., description="所属城市")
    category: str = Field(..., description="知识类别（如预约规则、避坑指南、独家机位、美食体验）")
    title: str = Field(..., description="知识标题")
    content: str = Field(..., description="正文内容")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    source: str = Field(default="", description="来源文件名")


class TravelRAGService:
    """
    轻量、稳健、可扩展的旅行知识库 RAG 检索器
    
    特点：
    - 针对中文旅游实体与偏好优化（无需依赖外部 Embedding API，与 DeepSeek 等任意模型即插即用）
    - 结合城市物理隔离、分类优先级（避坑/预约优先）以及用户偏好向量加权
    - 支持热重载与动态扩展知识文件
    """

    def __init__(self, knowledge_dir: Optional[str] = None):
        if knowledge_dir:
            self.knowledge_dir = Path(knowledge_dir)
        else:
            # 默认指向 backend/data/knowledge
            self.knowledge_dir = Path(__file__).parent.parent.parent / "data" / "knowledge"

        self.chunks: List[KnowledgeChunk] = []
        self._load_and_index()

    def _load_and_index(self):
        """扫描并解析知识库目录下的所有 Markdown 文件"""
        self.chunks.clear()

        if not self.knowledge_dir.exists():
            print(f"⚠️  知识库目录不存在: {self.knowledge_dir}，尝试自动创建...")
            self.knowledge_dir.mkdir(parents=True, exist_ok=True)
            return

        md_files = list(self.knowledge_dir.glob("*.md"))
        print(f"📚 开始加载知识库文件，共发现 {len(md_files)} 个文档...")

        chunk_counter = 0
        for file_path in md_files:
            city_name = file_path.stem.lower()
            # 城市名映射，如 beijing -> 北京
            city_map = {
                "beijing": "北京",
                "xian": "西安",
                "chengdu": "成都",
                "nanchang": "南昌",
                "shanghai": "上海",
                "hangzhou": "杭州",
                "guangzhou": "广州",
                "shenzhen": "深圳",
            }
            city_display = city_map.get(city_name, file_path.stem)

            try:
                content = file_path.read_text(encoding="utf-8")
                parsed_chunks = self._parse_markdown(content, city_display, file_path.name)
                for chunk in parsed_chunks:
                    chunk_counter += 1
                    chunk.id = f"{city_name}_{chunk_counter}"
                    self.chunks.append(chunk)
            except Exception as e:
                print(f"❌ 读取知识文件 {file_path.name} 失败: {e}")

        print(f"✅ 知识库加载完成，累计切分索引 {len(self.chunks)} 条攻略切片！")

    def _parse_markdown(self, text: str, city: str, filename: str) -> List[KnowledgeChunk]:
        """
        基于 Markdown 二级标题 (##) 进行结构化语义切块
        识别格式如: ## [分类] 标题
        提取正文中的: - **标签**：tag1, tag2
        """
        chunks: List[KnowledgeChunk] = []
        # 按二级标题切分
        sections = re.split(r'\n(?=##\s+)', text)

        for sec in sections:
            sec = sec.strip()
            if not sec or not sec.startswith("##"):
                continue

            lines = sec.split("\n")
            header = lines[0].replace("##", "").strip()

            # 解析 [类别] 标题
            category = "综合攻略"
            title = header
            cat_match = re.search(r'\[(.*?)\]\s*(.*)', header)
            if cat_match:
                category = cat_match.group(1).strip()
                title = cat_match.group(2).strip()

            body_lines = lines[1:]
            tags: List[str] = []
            content_lines: List[str] = []

            for line in body_lines:
                # 尝试提取标签（兼容 - **标签**：xxx 或 标签: xxx 格式）
                tag_match = re.search(r'[\*\-\s]*标签[\*\-\s]*[：:]\s*(.*)', line)
                if tag_match:
                    raw_tags = tag_match.group(1).replace("*", "").strip()
                    tags = [t.strip() for t in re.split(r'[,，、\s]+', raw_tags) if t.strip()]
                else:
                    if line.strip():
                        content_lines.append(line.strip())

            content = "\n".join(content_lines).strip()
            if not content:
                continue

            chunks.append(KnowledgeChunk(
                id="",
                city=city,
                category=category,
                title=title,
                content=content,
                tags=tags,
                source=filename
            ))

        return chunks

    def search(
        self,
        city: str,
        query: str = "",
        preferences: Optional[List[str]] = None,
        top_k: int = 4
    ) -> List[KnowledgeChunk]:
        """
        多维混合检索：
        1. 城市硬过滤（或高权重强绑定）
        2. 类别优先级加权（避坑、预约等安全类知识提权）
        3. 用户偏好（preferences）标签匹配度加权
        4. 关键词（query）文本相关度评分
        """
        if not self.chunks:
            return []

        preferences = preferences or []
        city_clean = city.replace("市", "").strip()

        scored_chunks = []
        for chunk in self.chunks:
            score = 0.0

            # 1. 城市匹配度检测
            is_city_match = (
                city_clean in chunk.city or
                chunk.city in city_clean or
                city_clean in chunk.title or
                city_clean in chunk.content
            )
            if not is_city_match:
                # 跨城市知识暂不推荐（保证高精度）
                continue
            
            # 基础分：同城知识基础分
            score += 10.0

            # 2. 核心风险类别加权：避坑和预约是刚需，能直接挽救旅行崩盘
            if "避坑" in chunk.category or "预约" in chunk.category or "政策" in chunk.category:
                score += 5.0
            if "独家机位" in chunk.category or "摄影" in chunk.category:
                if any(p in ["摄影", "网红", "拍照", "风景", "自然风光"] for p in preferences):
                    score += 6.0
            if "美食" in chunk.category:
                if any(p in ["美食", "小吃", "餐饮", "吃"] for p in preferences):
                    score += 6.0

            # 3. 用户偏好标签匹配
            for pref in preferences:
                pref_clean = pref.strip()
                if not pref_clean:
                    continue
                # 命中标签
                for tag in chunk.tags:
                    if pref_clean in tag or tag in pref_clean:
                        score += 4.0
                # 命中标题或正文
                if pref_clean in chunk.title:
                    score += 3.0
                if pref_clean in chunk.content:
                    score += 1.5

            # 4. 自由输入 Query 词匹配
            if query:
                # 拆分关键词（简单结巴分词理念：提取2字以上词素）
                words = [w for w in re.split(r'[,，、\s\-_]+', query) if len(w) >= 2]
                for w in words:
                    if w in chunk.title:
                        score += 3.0
                    if w in chunk.content:
                        score += 1.0

            scored_chunks.append((score, chunk))

        # 按得分从高到低排序
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        # 返回前 top_k 个切片
        results = [item[1] for item in scored_chunks[:top_k]]
        return results

    def format_for_agent(self, chunks: List[KnowledgeChunk]) -> str:
        """
        把检索到的切片转化为规整、便于 LLM 理解的 Markdown 提示词片段
        """
        if not chunks:
            return "暂未检索到该城市的特定避坑与专属攻略知识。"

        formatted_lines = [
            "### 💡 本地专属避坑与深度游玩指南 (来自旅行知识库真实经验):"
        ]

        for i, chunk in enumerate(chunks, 1):
            formatted_lines.append(f"{i}. **[{chunk.category}] {chunk.title}**")
            # 缩进正文
            indented_content = "\n".join(f"   {line}" for line in chunk.content.split("\n"))
            formatted_lines.append(indented_content)

        formatted_lines.append(
            "\n**【重要规划指令】**：请务必参考上述避坑指南和预约规则安排行程（例如避开周一闭馆、提醒提前预约放票、优先避开游客黑车及劣质美食街）。"
        )
        return "\n".join(formatted_lines)


# ---------------- 全局单例入口 ----------------
_rag_service_instance: Optional[TravelRAGService] = None


def get_rag_service() -> TravelRAGService:
    """获取 RAG 服务全局单例"""
    global _rag_service_instance
    if _rag_service_instance is None:
        _rag_service_instance = TravelRAGService()
    return _rag_service_instance
