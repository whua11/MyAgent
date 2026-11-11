"""
【SSE 极简用户版】只向前端推送干净的人性化进度提示
"""
import sys
import time
from typing import TypedDict

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from langgraph.graph import StateGraph, START, END

class DemoState(TypedDict):
    city: str
    weather: str
    rag_tips: str
    final_result: str

def weather_node(state: DemoState) -> dict:
    time.sleep(1.0)
    return {"weather": "北京晴好，22℃"}

def rag_node(state: DemoState) -> dict:
    time.sleep(1.2)
    return {"rag_tips": "故宫周一闭馆，需提前7天在小程序预约"}

def planner_node(state: DemoState) -> dict:
    time.sleep(1.5)
    return {"final_result": "规划完成"}

workflow = StateGraph(DemoState)
workflow.add_node("weather_node", weather_node)
workflow.add_node("rag_node", rag_node)
workflow.add_node("planner_node", planner_node)

workflow.add_edge(START, "weather_node")
workflow.add_edge(START, "rag_node")
workflow.add_edge("weather_node", "planner_node")
workflow.add_edge("rag_node", "planner_node")
workflow.add_edge("planner_node", END)

app = workflow.compile()

# ★ 极简推流函数：只给前端发送用户看得懂的一句话
def generate_clean_sse(city: str):
    initial_state = {"city": city, "weather": "", "rag_tips": "", "final_result": ""}
    
    # 建立友好文案字典
    node_messages = {
        "weather_node": "⛅ [天气服务] 高德实时天气查询完毕！",
        "rag_node": "📚 [知识库RAG] 本地避坑与门票规则检索命中！",
        "planner_node": "🤖 [大模型规划] DeepSeek 综合行程规划生成完毕！"
    }

    for chunk in app.stream(initial_state, stream_mode="updates"):
        for node_name in chunk.keys():
            msg = node_messages.get(node_name)
            if msg:
                # 遵循 SSE 协议：以 data: 开头，以 \n\n 结尾
                yield f"data: {msg}\n\n"

if __name__ == "__main__":
    print("=== 前端用户将在界面上实时看到的效果 ===\n")
    start = time.time()
    
    # 模拟前端 EventSource 接收到的信息
    for sse_frame in generate_clean_sse("北京"):
        elapsed = time.time() - start
        # 提取 data: 后面的正文
        clean_text = sse_frame.replace("data: ", "").strip()
        print(f"[{elapsed:.1f}s] 前端界面弹出提示 -> {clean_text}")
