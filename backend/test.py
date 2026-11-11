from app.models.schemas import Attraction

# 故意喂脏数据
a1 = Attraction(name="故宫", address="北京", location={"longitude":116.4,"latitude":39.9},
                visit_duration=120, description="测试", ticket_price="60元")
print(a1.ticket_price)   # 期望输出: 60

a2 = Attraction(name="故宫", address="北京", location={"longitude":116.4,"latitude":39.9},
                visit_duration=120, description="测试", ticket_price="免费")
print(a2.ticket_price)   # 期望输出: 0
