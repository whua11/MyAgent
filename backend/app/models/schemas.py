"""
【学习注释版】数据模型（前后端的“合同”）

这个文件做什么？
- 用 Pydantic 定义请求/响应结构
- FastAPI 靠它自动校验 JSON、生成 /docs 文档
- Agent 最终也要把 LLM 输出解析成这里的 TripPlan

谁在用？
- trip.py：参数 TripRequest，返回 TripPlanResponse
- trip_planner_agent.py：构造/返回 TripPlan、DayPlan、Attraction...
- 前端 types/index.ts：有一套对应的 TypeScript 接口（字段基本对齐）

学习重点：
1. TripRequest  = 输入
2. TripPlan     = 输出核心
3. TripPlanResponse = 给前端的外壳 {success, message, data}
"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator
from datetime import date  # 当前文件未直接使用，可忽略


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """
    旅行规划请求（前端 POST /api/trip/plan 的 body）

    对应前端：
    - Home.vue 组装的 requestData
    - types/index.ts 的 TripFormData
    """
    # Field(...): ... 表示必填
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    # ge=1, le=30：数值范围校验（小于1或大于30会 422）
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    # default=[]：不传就空列表
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    task_id: Optional[str] = Field(default=None, description="任务唯一标识/幂等键(用于故障恢复与断点续跑)", example="task_bj_20261115_001")

    class Config:
        # 给 /docs 提供示例，方便你在 Swagger 里一键试用
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI 搜索请求（地图相关接口用，主流程可不先看）"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求（地图相关接口用，主流程可不先看）"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """
    地理位置
    用途：Result.vue 地图打点需要 longitude/latitude
    """
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """单个景点（出现在 DayPlan.attractions 里）"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元)")
    @field_validator('ticket_price', mode='before')   # 1. 装饰器：指定字段 + 校验前执行
    @classmethod
    def parse_price(cls, v):                          # 2. 函数：收到原始值 v
        """
        门票价格清洗器：在赋值前执行
        作用：把 "免费" / "50元" 转成 int 0 / 50，避免校验失败
        """
        if v is None or v == "":
            return 0
        elif isinstance(v, float):
            return int(v)
        elif isinstance(v, int):
            return v
        elif isinstance(v, str):
            if v in ["免费", "免票"]:
                return 0
            else:
                # 尝试提取数字部分
                import re
                match = re.search(r'\d+', v)
                if match:
                    return int(match.group())
                else:
                    return 0
                    
class Meal(BaseModel):
    """单餐推荐（早/中/晚/小吃）"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")


class Hotel(BaseModel):
    """酒店推荐（挂在某一天的 day.hotel）"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")  # 注意：这里是字符串，LLM 常返回数字，解析时会转 str
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class DayPlan(BaseModel):
    """
    单日行程
    前端 Result.vue 会按 days 循环渲染“第 N 天”
    """
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """单日天气（TripPlan.weather_info 数组元素）"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    # 允许 int 或 str：因为 LLM 可能返回 "34°C"
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """
        温度清洗器：在赋值前执行
        作用：把 "34°C" / "34℃" 转成 int 34，避免校验失败
        """
        if isinstance(v, str):
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算汇总（结果页预算卡片）"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class TripPlan(BaseModel):
    """
    旅行计划（业务核心输出）

    产生位置：
    - trip_planner_agent._parse_response() 成功时
    - 或 _create_fallback_plan() 兜底时

    消费位置：
    - trip.py 放进 TripPlanResponse.data
    - 前端 sessionStorage + Result.vue 渲染
    """
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    tips: Optional[List[str]] = Field(default_factory=list, description="达人避坑与游玩贴士(来自RAG知识库)")
    trace_id: Optional[str] = Field(default=None, description="链路追踪唯一标识号 (Trace ID)")
    task_id: Optional[str] = Field(default=None, description="任务唯一标识号 (Task ID)")


class TripPlanResponse(BaseModel):
    """
    接口响应外壳
    前端判断：response.success && response.data
    """
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")
    trace_id: Optional[str] = Field(default=None, description="链路追踪唯一标识号 (Trace ID)")
    task_id: Optional[str] = Field(default=None, description="任务唯一标识号 (Task ID)")


class POIInfo(BaseModel):
    """POI 信息（地图搜索接口用）"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")


class POISearchResponse(BaseModel):
    """POI 搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """统一错误响应结构（文档/约定用）"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")
