import json
from pathlib import Path
import re

dataset = [
    {
        "id": "eval_bj_01",
        "city": "北京",
        "question": "周一去故宫玩可以买到票进馆吗？",
        "gold_anchor": "除法定节假日外，全年每周一全天闭馆",
        "expected_cause": "规则约束识别（闭馆时间）",
        "is_answerable": True
    },
    {
        "id": "eval_bj_02",
        "city": "北京",
        "question": "紫禁城门票怎么在网上买？提前几天几点放票？",
        "gold_anchor": "必须提前7天20:00在\"故宫博物院\"微信小程序预约购票",
        "expected_cause": "同义词别名替换（紫禁城 vs 故宫博物院）",
        "is_answerable": True
    },
    {
        "id": "eval_bj_03",
        "city": "北京",
        "question": "从北京去八达岭长城坐什么车最快不踩坑？",
        "gold_anchor": "清河站或北京北站坐高铁直达“八达岭长城站”",
        "expected_cause": "交通选型避坑",
        "is_answerable": True
    },
    {
        "id": "eval_bj_04",
        "city": "北京",
        "question": "国家博物馆每天几点放票？能带超过两万毫安的充电宝进去吗？",
        "gold_anchor": "每天17:00放票",
        "expected_cause": "细粒度放票时间与安检规则",
        "is_answerable": True
    },
    {
        "id": "eval_bj_05",
        "city": "北京",
        "question": "在北京环球影城想少排队玩哈利波特有什么省时技巧？",
        "gold_anchor": "单人通道（Single Rider）排队速度远快于普通通道",
        "expected_cause": "游玩攻略技巧检索",
        "is_answerable": True
    },
    {
        "id": "eval_cd_01",
        "city": "成都",
        "question": "去成都大熊猫繁育研究基地看花花，几点进园最好？",
        "gold_anchor": "早上7:30~8:00开园就入场",
        "expected_cause": "最佳游览时间策略",
        "is_answerable": True
    },
    {
        "id": "eval_cd_02",
        "city": "成都",
        "question": "成都大熊猫基地为什么要买观光车？",
        "gold_anchor": "买30元景区观光车直达山顶月亮产房",
        "expected_cause": "园区游览动线",
        "is_answerable": True
    },
    {
        "id": "eval_cd_03",
        "city": "成都",
        "question": "去青城山前山和后山有什么区别？门票各多少钱？",
        "gold_anchor": "前山主打道教宫观与人文底蕴（门票80元）",
        "expected_cause": "多实体数值对比",
        "is_answerable": True
    },
    {
        "id": "eval_cd_04",
        "city": "成都",
        "question": "人民公园鹤鸣茶社采耳一般多少钱？怎么防止被多收钱？",
        "gold_anchor": "普通采耳是30到40元",
        "expected_cause": "小红书长文消费避坑",
        "is_answerable": True
    },
    {
        "id": "eval_cd_05",
        "city": "成都",
        "question": "在成都春熙路IFS怎么拍那只爬墙大熊猫最出片还不用从一楼慢慢逛？",
        "gold_anchor": "坐直梯直接到7楼露天花园",
        "expected_cause": "具体机位指引",
        "is_answerable": True
    },
    {
        "id": "eval_cd_06",
        "city": "成都",
        "question": "不能吃辣的人吃四川老火锅应该怎么调味碟？",
        "gold_anchor": "香油蒜泥味碟",
        "expected_cause": "民俗美食常识",
        "is_answerable": True
    },
    {
        "id": "eval_cq_01",
        "city": "重庆",
        "question": "洪崖洞几点开灯？最佳拍摄全景的机位在哪里？",
        "gold_anchor": "千厮门大桥人行道",
        "expected_cause": "夜景时间与全景机位",
        "is_answerable": True
    },
    {
        "id": "eval_cq_02",
        "city": "重庆",
        "question": "洪崖洞的1楼和11楼分别出来是什么马路？",
        "gold_anchor": "1层出来是嘉陵江滨江路，11层出来是大马路（沧白路）",
        "expected_cause": "8D魔幻地形空间关系",
        "is_answerable": True
    },
    {
        "id": "eval_cq_03",
        "city": "重庆",
        "question": "在重庆坐长江索道怎么避免在现场排队一两个小时？",
        "gold_anchor": "提前在“长江索道”官方公众号线上排队叫号",
        "expected_cause": "排队策略优化",
        "is_answerable": True
    },
    {
        "id": "eval_hz_01",
        "city": "杭州",
        "question": "去杭州灵隐寺一共需要买几张门票？分别叫什么名字？",
        "gold_anchor": "必须买两道门票",
        "expected_cause": "门票套路拆解",
        "is_answerable": True
    },
    {
        "id": "eval_hz_02",
        "city": "杭州",
        "question": "西湖如果不想去断桥人挤人，本地人推荐哪些清幽的景致？",
        "gold_anchor": "茅家埠、乌龟潭、浴鹄湾或九溪烟树",
        "expected_cause": "冷门小众景点推荐",
        "is_answerable": True
    },
    {
        "id": "eval_nc_01",
        "city": "南昌",
        "question": "在南昌滕王阁怎么背诗可以免门票？几分钟背完？",
        "gold_anchor": "6分钟内完整背诵全文",
        "expected_cause": "景区特殊免票规则",
        "is_answerable": True
    },
    {
        "id": "eval_nc_02",
        "city": "南昌",
        "question": "江西省博物馆和八一起义纪念馆周一开门吗？",
        "gold_anchor": "每周一全天闭馆",
        "expected_cause": "场馆开放周期",
        "is_answerable": True
    },
    {
        "id": "eval_nc_03",
        "city": "南昌",
        "question": "老南昌地道过早去哪里？拌粉和肉饼汤一般多少钱？",
        "gold_anchor": "大士院才是老南昌的美食灵魂",
        "expected_cause": "地道烟火气老街匹配",
        "is_answerable": True
    },
    {
        "id": "eval_sh_01",
        "city": "上海",
        "question": "上海迪士尼乐园可以带自拍杆或者小板凳入园吗？",
        "gold_anchor": "严禁携带自拍杆、折叠小板凳",
        "expected_cause": "园区安检禁带清单",
        "is_answerable": True
    },
    {
        "id": "eval_sh_02",
        "city": "上海",
        "question": "上海生煎清水派和混水派分别推荐哪家老字号？",
        "gold_anchor": "大壶春（鲜肉生煎、清水派无汤汁）或小杨生煎（混水派多汁）",
        "expected_cause": "细分品类老字号辨析",
        "is_answerable": True
    },
    {
        "id": "eval_xa_01",
        "city": "西安",
        "question": "兵马俑门票多少钱？没抢到票怎么捡漏？",
        "gold_anchor": "8:45和9:00未支付订单回流捡漏",
        "expected_cause": "抢票回流规则",
        "is_answerable": True
    },
    {
        "id": "eval_xa_02",
        "city": "西安",
        "question": "陕西历史博物馆如果免费票抢光了，有什么办法能正常进馆参观？",
        "gold_anchor": "直接购买30元的“大唐遗宝·何家村专题展”或特展门票",
        "expected_cause": "特殊保底购票方案",
        "is_answerable": True
    },
    {
        "id": "eval_neg_01",
        "city": "拉萨",
        "question": "去拉萨布达拉宫门票提前几天预约？现场可以买吗？",
        "gold_anchor": "",
        "expected_cause": "跨地域未收录（测试拒答）",
        "is_answerable": False
    },
    {
        "id": "eval_neg_02",
        "city": "杭州",
        "question": "在杭州灵隐寺可以办理西湖寺庙年卡退费吗？退费流程是怎样的？",
        "gold_anchor": "",
        "expected_cause": "未收录的行政退费业务（测试拒答）",
        "is_answerable": False
    },
    {
        "id": "eval_neg_03",
        "city": "北京",
        "question": "北京故宫博物院内可以带金毛等大型宠物犬入内吗？",
        "gold_anchor": "",
        "expected_cause": "未收录的宠物入园政策（测试拒答）",
        "is_answerable": False
    }
]

# Verify anchors against documents
data_dir = Path(r"E:\研究生\ai-agent\helloagents-trip-planner\backend\app\knowledge\data")
all_docs_text = ""
for f in data_dir.glob("*.md"):
    with open(f, "r", encoding="utf-8") as fp:
        all_docs_text += fp.read() + "\n"

clean_all_text = re.sub(r"\s+", "", all_docs_text)

print(f"Total items in dataset: {len(dataset)}")
for item in dataset:
    if item["is_answerable"]:
        anchor = re.sub(r"\s+", "", item["gold_anchor"])
        if anchor in clean_all_text:
            print(f"[OK] {item['id']}: anchor verified in documents.")
        else:
            print(f"[ERROR] {item['id']}: anchor NOT FOUND in docs: {item['gold_anchor']}")

out_path = Path(r"E:\研究生\ai-agent\helloagents-trip-planner\backend\app\knowledge\evaluation\rag_eval_dataset.json")
with open(out_path, "w", encoding="utf-8") as fp:
    json.dump(dataset, fp, ensure_ascii=False, indent=2)
print(f"\nSaved to {out_path}")
