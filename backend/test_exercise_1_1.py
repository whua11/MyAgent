"""
练习 1.1 的验收测试：Attraction.ticket_price 脏数据清洗

用法（在 backend 目录下）:
    python test_exercise_1_1.py

每行一个用例: (输入的 ticket_price, 期望输出)
全绿才算过关。标 [挑战] 的是选做，做不出来说明清洗策略还有升级空间。
"""

from app.models.schemas import Attraction

# 基础字段（ticket_price 之外都是合法值）
BASE = dict(
    name="故宫",
    address="北京市东城区",
    location={"longitude": 116.397, "latitude": 39.916},
    visit_duration=120,
    description="测试景点",
)

CASES = [
    # (输入, 期望值, 说明)
    ("60元", 60, "带单位的字符串"),
    ("60", 60, "纯数字字符串"),
    (60, 60, "本来就是 int"),
    (60.0, 60, "float 需要转 int"),
    ("免费", 0, "免费要变 0"),
    ("免票", 0, "免票也要变 0"),
    ("", 0, "空字符串"),
    (None, 0, "None 也不能炸"),
    ("很贵", 0, "完全认不出的值给 0"),
    ("80元/人", 80, "[挑战] 按人收费的写法"),
    ("票价60-80元", {60, 80}, "[挑战] 价格区间取上界还是下界?你自己定,但别返回0"),
]


def run():
    passed, failed = 0, 0
    for raw, expected, desc in CASES:
        try:
            a = Attraction(**BASE, ticket_price=raw)
            actual = a.ticket_price
            # expected 是 set 时,命中任意一个都算过(价格区间取上/下界都对)
            ok = actual in expected if isinstance(expected, set) else actual == expected
            if ok:
                print(f"  PASS  {desc:<20} {raw!r:>12} -> {actual}")
                passed += 1
            else:
                print(f"  FAIL  {desc:<20} {raw!r:>12} -> {actual} (期望 {expected})")
                failed += 1
        except Exception as e:
            print(f"  FAIL  {desc:<20} {raw!r:>12} -> 抛异常: {type(e).__name__}")
            failed += 1

    print(f"\n结果: {passed} 通过 / {failed} 失败")
    return failed == 0


if __name__ == "__main__":
    run()
