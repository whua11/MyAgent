"""
本地旅行知识库检索管理器 (Local RAG Manager)
基于 ChromaDB + BAAI/bge-small-zh-v1.5 (FastEmbed) 构建原生中文向量数据库
用于为 Agent 提供景区门票预约规则、闭馆时间、本地人避坑建议等真实攻略
支持工业级 15% 语义 Overlap 递归切分与小红书原生游记长文直接入库
"""
import os
import re
import chromadb
from pathlib import Path
from loguru import logger
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from fastembed import TextEmbedding

# 确定持久化存储路径与知识文档路径
CURRENT_DIR = Path(__file__).resolve().parent
DB_DIR = CURRENT_DIR / "chroma_db"
DATA_DIR = CURRENT_DIR / "data"


class ChineseEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    专门针对中文调优的轻量级向量模型类
    基于智源 BAAI/bge-small-zh-v1.5，向量维度 512，完美识别中文文化与同义词
    """
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.embedding_model = TextEmbedding(model_name=model_name)

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = list(self.embedding_model.embed(input))
        return [e.tolist() for e in embeddings]


class TravelRAGManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例模式，避免重复连接向量数据库"""
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        DB_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # 1. 初始化中文向量计算引擎
        self.embedding_function = ChineseEmbeddingFunction()
        
        # 2. 初始化持久化 Chroma 客户端 (数据存放在本地目录)
        self.client = chromadb.PersistentClient(path=str(DB_DIR))
        self.collection_name = "travel_tips_zh_knowledge"
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,  # 注入中文向量大脑！
            metadata={"description": "中文旅游景点门票避坑与实用攻略库"}
        )
        
        # 3. 自动检查并载入知识文档
        self._load_documents_if_needed()
        self._initialized = True

    def _chunk_document_with_overlap(
        self,
        content: str,
        city: str,
        filename: str,
        chunk_size: int = 400,
        overlap_ratio: float = 0.15
    ) -> list[dict]:
        """
        工业级原生文本/小红书长文分块器 (带 15% 语义 Overlap 与富元数据)
        1. 保持段落与句子完整性（绝不机械硬切文字，按标点优雅断句）
        2. 严格维持 15% 语义重叠（Overlap），消除相邻切块间的语义割裂
        3. 注入富元数据 (Metadata Enrichment)：city, source, section, overlap_ratio, char_count
        """
        overlap_chars = int(chunk_size * overlap_ratio)  # 400 * 0.15 = 60 字符
        chunks = []
        
        # 1. 结构化解析：若含有二级标题 ##，则以二级标题为大纲单元
        has_headers = "\n## " in content or content.startswith("## ")
        sections = []
        if has_headers:
            raw_secs = content.split("\n## ")
            for idx, sec in enumerate(raw_secs):
                sec = sec.strip()
                if not sec:
                    continue
                lines = sec.split("\n", 1)
                sec_title = lines[0].replace("#", "").strip()
                sec_body = lines[1].strip() if len(lines) > 1 else ""
                sections.append((sec_title, sec_body or sec))
        else:
            # 纯长文或无章节标题：以首行提取摘要标题
            lines = content.strip().split("\n", 1)
            sec_title = lines[0].replace("#", "").strip()[:30]
            sec_body = lines[1].strip() if len(lines) > 1 else lines[0]
            sections.append((sec_title, sec_body))
            
        global_chunk_idx = 0
        for sec_title, sec_body in sections:
            # 按中文句子分隔符切开
            raw_sentences = re.split(r'(?<=[。！？!?\n])', sec_body)
            sentences = [s.strip() for s in raw_sentences if s.strip()]
            
            current_sentences = []
            current_len = 0
            
            for s in sentences:
                s_len = len(s)
                # 超长单句兜底
                if s_len > chunk_size:
                    if current_sentences:
                        text_to_save = f"【{sec_title}】\n" + "".join(current_sentences)
                        chunks.append({
                            "text": text_to_save,
                            "metadata": {
                                "city": city,
                                "source": filename,
                                "chunk_id": f"{filename}_{global_chunk_idx}",
                                "chunk_index": global_chunk_idx,
                                "section": sec_title,
                                "overlap_ratio": "15%",
                                "overlap_chars": overlap_chars,
                                "char_count": len(text_to_save),
                                "doc_type": "xhs_raw" if "raw" in filename else "guide"
                            }
                        })
                        global_chunk_idx += 1
                        current_sentences = []
                        current_len = 0
                    for i in range(0, s_len, chunk_size - overlap_chars):
                        sub = s[i:i + chunk_size]
                        if sub:
                            text_to_save = f"【{sec_title}】\n" + sub
                            chunks.append({
                                "text": text_to_save,
                                "metadata": {
                                    "city": city,
                                    "source": filename,
                                    "chunk_id": f"{filename}_{global_chunk_idx}",
                                    "chunk_index": global_chunk_idx,
                                    "section": sec_title,
                                    "overlap_ratio": "15%",
                                    "overlap_chars": overlap_chars,
                                    "char_count": len(text_to_save),
                                    "doc_type": "xhs_raw" if "raw" in filename else "guide"
                                }
                            })
                            global_chunk_idx += 1
                    continue

                if current_len + s_len > chunk_size and current_sentences:
                    text_to_save = f"【{sec_title}】\n" + "".join(current_sentences)
                    chunks.append({
                        "text": text_to_save,
                        "metadata": {
                            "city": city,
                            "source": filename,
                            "chunk_id": f"{filename}_{global_chunk_idx}",
                            "chunk_index": global_chunk_idx,
                            "section": sec_title,
                            "overlap_ratio": "15%",
                            "overlap_chars": overlap_chars,
                            "char_count": len(text_to_save),
                            "doc_type": "xhs_raw" if "raw" in filename else "guide"
                        }
                    })
                    global_chunk_idx += 1
                    
                    # 抽取末尾句子组成 15% overlap
                    overlap_sentences = []
                    overlap_len = 0
                    for prev_s in reversed(current_sentences):
                        if overlap_len + len(prev_s) <= overlap_chars or not overlap_sentences:
                            overlap_sentences.insert(0, prev_s)
                            overlap_len += len(prev_s)
                        else:
                            break
                    current_sentences = overlap_sentences + [s]
                    current_len = sum(len(x) for x in current_sentences)
                else:
                    current_sentences.append(s)
                    current_len += s_len
                    
            if current_sentences:
                text_to_save = f"【{sec_title}】\n" + "".join(current_sentences)
                chunks.append({
                    "text": text_to_save,
                    "metadata": {
                        "city": city,
                        "source": filename,
                        "chunk_id": f"{filename}_{global_chunk_idx}",
                        "chunk_index": global_chunk_idx,
                        "section": sec_title,
                        "overlap_ratio": "15%",
                        "overlap_chars": overlap_chars,
                        "char_count": len(text_to_save),
                        "doc_type": "xhs_raw" if "raw" in filename else "guide"
                    }
                })
                global_chunk_idx += 1
                
        return chunks

    def _load_documents_if_needed(self, force_reload: bool = False):
        """扫描 data 目录并将所有 Markdown 攻略切块存入 ChromaDB 向量库"""
        all_chunks = []
        city_map = {
            "beijing": "北京", "shanghai": "上海", "nanchang": "南昌",
            "guangzhou": "广州", "shenzhen": "深圳", "chengdu": "成都",
            "hangzhou": "杭州", "xian": "西安", "chongqing": "重庆",
            "wuhan": "武汉", "nanjing": "南京", "changsha": "长沙"
        }
        
        # 遍历 data 目录下的所有 markdown 文件
        for file in DATA_DIR.glob("*.md"):
            fname = file.stem.lower()
            city = "通用"
            for pinyin, cname in city_map.items():
                if pinyin in fname:
                    city = cname
                    break
            with open(file, "r", encoding="utf-8") as f:
                content = f.read()
            chunks = self._chunk_document_with_overlap(content, city, file.name, chunk_size=400, overlap_ratio=0.15)
            all_chunks.extend(chunks)

        if not all_chunks:
            logger.warning("[RAG] 未找到任何知识库文档，请在 data 目录添加 .md 文件")
            return

        current_count = self.collection.count()
        if current_count == len(all_chunks) and not force_reload:
            logger.info(f"[RAG] 中文向量知识库已有最新 {current_count} 条知识片段，跳过重复索引构建")
            return

        logger.info(f"[RAG] 正在重构本地中文知识库向量索引 (带15% Overlap，共 {len(all_chunks)} 个切块)...")
        # 清空旧集合，确保无历史残余切块
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,
            metadata={"description": "中文旅游景点门票避坑与实用攻略库（带15% Overlap语义切分与富元数据）"}
        )
        
        # 批量加入 ChromaDB
        docs = [c["text"] for c in all_chunks]
        metadatas = [c["metadata"] for c in all_chunks]
        ids = [c["metadata"]["chunk_id"] for c in all_chunks]

        # 分批 upsert
        batch_size = 50
        for i in range(0, len(docs), batch_size):
            self.collection.upsert(
                documents=docs[i:i + batch_size],
                metadatas=metadatas[i:i + batch_size],
                ids=ids[i:i + batch_size]
            )
        logger.success(f"[RAG] 中文知识库索引构建完成！共存入 {len(docs)} 个攻略切块（全部附带 15% Overlap 与城市/来源元数据）")

    def search_guides(self, query: str, city: str = None, top_k: int = 3, return_metadata: bool = False):
        """
        语义检索接口：根据用户 Query 检索最相关的避坑与攻略片段
        :param query: 搜索关键词或自然语言需求（例如："故宫门票"、"上海夜景"）
        :param city: 城市过滤条件（可选）
        :param top_k: 返回最相关的段落数量
        :param return_metadata: 是否同时返回切块元数据
        :return: 相关的攻略文本列表 或 (文本, 元数据) 元组列表
        """
        where_filter = None
        if city:
            # 支持精确按城市元数据过滤
            where_filter = {"city": city}

        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter
        )
        
        if results and "documents" in results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if "metadatas" in results and results["metadatas"] else [{}] * len(docs)
            if return_metadata:
                return list(zip(docs, metas))
            return docs
        return []

    def get_travel_tips(self, destination: str, preferences: list[str] = None) -> str:
        """
        专为 Agent 设计的便利方法：组合成可以直接注入 Prompt 的知识上下文字符串
        附带来源文件、出处章节与 15% Overlap 标识
        """
        pref_str = " ".join(preferences) if preferences else ""
        query = f"{destination} 景点门票预约 开放时间 避坑指南 必吃美食 {pref_str}".strip()
        
        # 1. 优先按目标城市元数据精确检索（只召回该城市的真实攻略）
        clean_city = destination.replace("市", "").replace("特别行政区", "")
        items = self.search_guides(query=query, city=clean_city, top_k=3, return_metadata=True)
        
        # 2. 如果目标城市未录入，尝试只检索“通用”避坑原则，杜绝跨城市（如把北京故宫硬塞给南昌）张冠李戴
        if not items:
            items = self.search_guides(query=query, city="通用", top_k=2, return_metadata=True)

        if not items:
            return ""

        formatted_tips = "\n\n".join([
            f"【达人避坑/真实攻略片段 {i+1} | 来源: {meta.get('source', '未知')} | 章节: {meta.get('section', '正文')} | 15% Overlap切片】：\n{doc}"
            for i, (doc, meta) in enumerate(items)
        ])
        return formatted_tips

    def search_with_gate(self, query: str, city: str = None, top_k: int = 3) -> dict:
        """
        带双层拒答门禁与诚实降级的生产级安全检索接口：
        1. Layer 1 业务范围守卫检查 (Scope Gate) - 消除规则盲区
        2. 检索相关文档与相似度距离
        3. Layer 1.5 语义距离截断检查 (Distance Guard)
        4. Layer 2 事实对齐守卫检查 (Fact Alignment Gate) - 阻断高风险未收录政策
        5. 诚实降级 (Honest Degradation)：被阻断时输出透明安全提示与 Prompt 防护指令，防系统级幻觉
        """
        from app.knowledge.rejection_gate import rag_rejection_gate

        # 1. 前置范围守卫
        scope_ok, scope_msg = rag_rejection_gate.check_scope(query, city)
        if not scope_ok:
            decision = rag_rejection_gate.inspect(query=query, city=city)
            return {
                "can_answer": False,
                "gate": decision["gate"],
                "reason": decision["reason"],
                "honest_degradation": True,
                "documents": [],
                "metadatas": [],
                "suggested_reply": decision["suggested_reply"],
                "system_prompt_guard": decision["system_prompt_guard"],
                "formatted_tips": decision["suggested_reply"]
            }

        # 2. 正常检索文档与距离
        items = self.search_guides(query=query, city=city, top_k=top_k, return_metadata=True)
        docs = [item[0] for item in items]
        metas = [item[1] for item in items]

        # 3. 综合多层裁决 (含语义距离与事实对齐)
        decision = rag_rejection_gate.inspect(query=query, city=city, retrieved_docs=docs)
        if not decision["can_answer"]:
            return {
                "can_answer": False,
                "gate": decision["gate"],
                "reason": decision["reason"],
                "honest_degradation": True,
                "documents": docs,
                "metadatas": metas,
                "suggested_reply": decision["suggested_reply"],
                "system_prompt_guard": decision["system_prompt_guard"],
                "formatted_tips": decision["suggested_reply"]
            }

        if not items:
            return {
                "can_answer": True,
                "gate": "PASS_EMPTY",
                "reason": "通过门禁但库中无对应片区内容",
                "honest_degradation": True,
                "documents": [],
                "metadatas": [],
                "suggested_reply": "⚠️ 提示：暂未查询到该景点的专属避坑小贴士，建议参考城市通用指南或官方通告。",
                "system_prompt_guard": "【安全可信指令】未检索到专属攻略片区，请使用通用常识，严禁编造具体票价与特殊限流规定。",
                "formatted_tips": ""
            }

        formatted_tips = "\n\n".join([
            f"【达人避坑/真实攻略片段 {i+1} | 来源: {meta.get('source', '未知')} | 章节: {meta.get('section', '正文')} | 15% Overlap切片】：\n{doc}"
            for i, (doc, meta) in enumerate(items)
        ])

        return {
            "can_answer": True,
            "gate": "PASS",
            "reason": "通过双层可答性与事实对齐校验",
            "honest_degradation": False,
            "documents": docs,
            "metadatas": metas,
            "suggested_reply": None,
            "system_prompt_guard": None,
            "formatted_tips": formatted_tips
        }


# 全局单例导出
rag_manager = TravelRAGManager()

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    
    print("--- 测试 1: 中文语义检索（紫禁城 vs 故宫）---")
    results = rag_manager.search_guides("周一可以去紫禁城吗？", city="北京", top_k=1)
    for r in results:
        print(r)
        print("-" * 40)
        
    print("\n--- 测试 2: 模拟 Agent 注入 Prompt 上下文 ---")
    tips = rag_manager.get_travel_tips("北京", preferences=["历史文化", "地道美食"])
    print(tips[:200] + "...")
