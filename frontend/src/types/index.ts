// 【学习注释版】前端 TypeScript 类型定义
//
// 这个文件做什么？
// - 给前端对象加“形状约束”，减少字段写错
// - 和后端 schemas.py 基本一一对应（前后端合同）
//
// 谁在用？
// - Home.vue：TripFormData
// - api.ts：TripFormData / TripPlanResponse
// - Result.vue：TripPlan

/** 经纬度：地图打点用 */
export interface Location {
  longitude: number
  latitude: number
}

/** 单个景点 */
export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
}

/** 单餐 */
export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
}

/** 酒店 */
export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

/** 预算汇总 */
export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

/** 单日行程：Result.vue 会按 days 循环渲染 */
export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

/** 单日天气 */
export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

/**
 * 完整旅行计划（后端 data 字段）
 * Home.vue 成功后：sessionStorage.setItem('tripPlan', JSON.stringify(data))
 * Result.vue：JSON.parse 后赋给 tripPlan
 */
export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
  tips?: string[]
  trace_id?: string
}

/**
 * 前端发给后端的请求体
 * 对应后端 TripRequest
 */
export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
}

/**
 * 后端接口外壳
 * api.ts：post<TripPlanResponse>(...)
 * Home.vue：判断 response.success && response.data
 */
export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
  trace_id?: string
}
