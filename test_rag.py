"""
【RAG 专项练习】第 3 关：将 RAG 封装为 LangGraph 节点并驱动大模型规划
"""
import sys
import os
from typing import TypedDict
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 载入环境变量（读取 DeepSeek API KEY）
load_dotenv(dotenv_path="backend/.env")

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from fastembed import TextEmbedding
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

# ==============================================================
# 1. 搭建中文向量库（复习第 1 关与第 2 关）
# ==============================================================
class ChineseEmbeddingFunction(EmbeddingFunction[Documents]):
    def __init__(self, model_name="BAAI/bge-small-zh-v1.5"):
        self.embedding_model = TextEmbedding(model_name=model_name)

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = list(self.embedding_model.embed(input))
        return [e.tolist() for e in embeddings]

zh_ef = ChineseEmbeddingFunction()
chroma_client = chromadb.Client()
collection = chroma_client.create_collection(
    name="travel_stage_3",
    embedding_function=zh_ef
)

# 存入北京旅游避坑库（带城市元数据）
collection.add(
    documents=[
        "## 故宫博物院避坑：除法定节假日外，全年每周一闭馆！必须提前7天20:00在小程序预约，现场不设售票窗口，严禁买黄牛票。",
        "## 八达岭长城交通：建议在清河站或北京北站坐高铁直达八达岭长城站仅需20分钟，千万别在德胜门坐黑大巴。",
        "## 北京美食避坑：王府井小吃街价格虚高且不正宗；想吃地道清真小吃建议去牛街，烤鸭优先推荐四季民福（需提前取号），不买真空包装。"
    ],
    metadatas=[{"city": "北京"}, {"city": "北京"}, {"city": "北京"}],
    ids=["bj_gugong", "bj_greatwall", "bj_food"]
)
print("✅ 本地避坑知识库初始化就绪！\n")


# ==============================================================
# 2. 定义 LangGraph 全局状态 (State)
# ==============================================================
class RAGTripState(TypedDict):
    destination: str        # 目的地
    days: int               # 天数
    user_query: str         # 用户的具体诉求
    rag_tips: str           # ★ RAG 节点查出来的避坑参考知识
    final_plan: str         # 大模型最终生成的行程计划


# ==============================================================
# 3. 编写两个核心节点：rag_node 与 planner_node
# ==============================================================

def rag_node(state: RAGTripState) -> dict:
    """
    RAG 节点：充当智能体的'案头参考书检索员'
    根据当前状态里的目的地和需求，去向量库翻出最相关的避坑规则
    """
    city = state["destination"]
    query = f"{city} 门票预约 开放时间 避坑指南 {state['user_query']}"
    print(f"📚 [RAG 节点执行中] 正在检索目的地【{city}】的避坑攻略...")
    
    # 使用第 2 关学到的 where 元数据精准过滤城市！
    results = collection.query(
        query_texts=[query],
        n_results=2,  # 捞出最相关的 2 条切块
        where={"city": city}
    )
    
    matched_docs = results["documents"][0]
    # 把捞出来的知识拼接成一个清晰的上下文文本
    tips_context = "\n".join([f"- {doc}" for doc in matched_docs])
    print(f"📖 [RAG 节点完成] 成功查到 {len(matched_docs)} 条真实攻略并存入 State！")
    
    # 状态机更新：将 rag_tips 字段写入 State
    return {"rag_tips": tips_context}


def planner_node(state: RAGTripState) -> dict:
    """
    规划节点：由 DeepSeek 充当大厨
    将用户需求与 RAG 查出来的避坑参考书融合，生成绝不踩坑的日程
    """
    print("🤖 [Planner 节点执行中] 唤醒 DeepSeek 大模型，结合 RAG 避坑指南开始规划...")
    
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL_ID", "deepseek-chat"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
        temperature=0.7
    )
    
    # 提示词工程：把 state["rag_tips"] 显式注入 Prompt
    prompt = f"""
你是一位金牌旅行规划师。请根据以下信息为用户制定 {state['days']} 天的日程：

【用户需求】
目的地：{state['destination']}
天数：{state['days']} 天
具体要求：{state['user_query']}

【本地真实避坑与预约指南（来自本地向量库知识）】
{state['rag_tips']}

请特别注意：
1. 行程必须严格遵守上述避坑指南（比如遇到闭馆日要提醒用户避开，门票预约要写明提前几天在哪里预约）；
2. 美食部分融入上述防坑建议；
3. 输出一份生动、专业、按上午/下午/晚上排布的简洁日程安排。
"""
    response = llm.invoke(prompt)
    return {"final_plan": response.content}


# ==============================================================
# 4. 构建并编译 LangGraph 状态机
# ==============================================================
workflow = StateGraph(RAGTripState)

# 注册两个节点
workflow.add_node("rag_node", rag_node)
workflow.add_node("planner_node", planner_node)

# 连接流程：START -> rag_node -> planner_node -> END
workflow.add_edge(START, "rag_node")
workflow.add_edge("rag_node", "planner_node")
workflow.add_edge("planner_node", END)

# 编译成可运行的 App
app = workflow.compile()
print("🚀 LangGraph + RAG 状态图编译完成！\n")


# ==============================================================
# 5. 启动测试
# ==============================================================
if __name__ == "__main__":
    initial_state: RAGTripState = {
        "destination": "北京",
        "days": 2,
        "user_query": "重点想去故宫走走，顺便吃吃地道美食，了解一些本地注意事项",
        "rag_tips": "",
        "final_plan": ""
    }
    
    print("--- 状态机启动 ---")
    final_output = app.invoke(initial_state)
    
    print("\n" + "="*50)
    print("🎉 最终大模型生成的完美日程：")
    print("="*50)
    print(final_output["final_plan"])
