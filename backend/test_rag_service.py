"""
【RAG 检索服务专项测试】
测试目标：
1. 验证 Markdown 知识库切分是否成功、结构化字段是否完整
2. 验证多维混合检索（城市过滤、偏好标签匹配、避坑加权）是否准确
3. 验证格式化输出给 Agent 的 Prompt 质量

执行方式（在 backend 目录下）:
    python test_rag_service.py
"""

import sys
import os

# Windows 控制台编码保护，避免 emoji 引发 GBK 编码报错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

# 确保 backend 目录在 sys.path 中
sys.path.insert(0, os.path.dirname(__file__))

from app.services.rag_service import TravelRAGService


def test_rag():
    print("=" * 60)
    print("🧪 开始 RAG 知识检索服务功能验证测试...")
    print("=" * 60)

    # 1. 实例化服务
    rag = TravelRAGService()
    print(f"\n[测试 1] 知识库切块统计: 成功加载并切分 {len(rag.chunks)} 条知识块")
    assert len(rag.chunks) >= 10, f"切片数量过少，期望 >= 10，实际 {len(rag.chunks)}"
    print("  -> PASS: 知识库文件解析切分正常！")

    # 2. 测试北京历史文化 + 摄影偏好
    print("\n[测试 2] 检索场景: 目的地【北京】，偏好【历史文化, 摄影】")
    bj_results = rag.search(city="北京", preferences=["历史文化", "摄影"], top_k=3)
    assert len(bj_results) > 0, "未检索到北京知识"
    for idx, c in enumerate(bj_results, 1):
        print(f"  [{idx}] 类别: {c.category:<6} | 标题: {c.title} | 标签: {c.tags}")
    
    # 验证是否命中了故宫或景山机位
    titles = [c.title for c in bj_results]
    assert any("故宫" in t or "景山" in t for t in titles), "期望命中故宫或景山机位"
    print("  -> PASS: 成功检索到北京历史文化与独家摄影机位！")

    # 3. 测试西安美食偏好
    print("\n[测试 3] 检索场景: 目的地【西安】，偏好【美食】")
    xian_results = rag.search(city="西安", preferences=["美食"], top_k=2)
    assert len(xian_results) > 0, "未检索到西安知识"
    for idx, c in enumerate(xian_results, 1):
        print(f"  [{idx}] 类别: {c.category:<6} | 标题: {c.title}")
    
    xian_titles = [c.title for c in xian_results]
    assert any("回民街" in t or "关中" in t or "陕历博" in t for t in xian_titles), "期望命中西安美食或避坑"
    print("  -> PASS: 成功精准匹配西安特色小吃与避坑！")

    # 4. 测试成都亲子/大熊猫
    print("\n[测试 4] 检索场景: 目的地【成都】，偏好【亲子, 休闲】，自由诉求【想看大熊猫花花】")
    cd_results = rag.search(city="成都", query="想看大熊猫花花", preferences=["亲子", "休闲"], top_k=2)
    assert len(cd_results) > 0, "未检索到成都知识"
    for idx, c in enumerate(cd_results, 1):
        print(f"  [{idx}] 类别: {c.category:<6} | 标题: {c.title}")
    assert any("大熊猫" in c.title for c in cd_results), "期望命中大熊猫早起攻略"
    print("  -> PASS: 成功命中大熊猫繁育研究基地早起秘籍！")

    # 5. 测试 Agent 格式化输出
    print("\n[测试 5] 测试 Agent 提示词转化效果:")
    formatted_prompt = rag.format_for_agent(bj_results)
    print("-" * 50)
    print(formatted_prompt[:300] + "\n... (略去后续)")
    print("-" * 50)
    assert "【重要规划指令】" in formatted_prompt, "格式化输出应包含规划指引"
    print("  -> PASS: 提示词格式化质量达标！")

    print("\n" + "=" * 60)
    print("🎉 ALL TESTS PASSED! RAG 检索模块第一步测试全绿！")
    print("=" * 60)


if __name__ == "__main__":
    test_rag()
