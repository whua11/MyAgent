/**
 * 【学习注释版】前端 API 封装层
 *
 * 这个文件做什么？
 * - 统一创建 axios 客户端（基地址、超时、JSON 头）
 * - 封装「生成旅行计划」「健康检查」两个接口函数
 *
 * 谁调用它？
 * - Home.vue：generateTripPlan(requestData)
 * - 可选：healthCheck()
 *
 * 它调用谁？
 * - 后端 FastAPI：POST /api/trip/plan 、 GET /health
 *
 * 数据流：
 * Home.vue formData
 *   -> 组装 TripFormData
 *   -> generateTripPlan()
 *   -> axios POST JSON
 *   -> 后端 trip.py plan_trip()
 *   -> 返回 TripPlanResponse
 *   -> Home.vue 把 response.data 存 sessionStorage
 */

import axios from 'axios'
// TripFormData: 发给后端的请求体类型
// TripPlanResponse: 后端返回的 { success, message, data? }
import type { TripFormData, TripPlanResponse } from '@/types'

// 从 frontend/.env 读取后端地址；没配就默认本机 8000
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// 创建一个可复用的 axios 实例（比每次 axios.post 写全参数更清晰）
const apiClient = axios.create({
  baseURL: API_BASE_URL, // 后续 url 写 '/api/trip/plan' 即可
  timeout: 120000, // 120 秒：后端要跑多 Agent + MCP，比较慢
  headers: {
    'Content-Type': 'application/json' // 告诉后端 body 是 JSON
  }
})

// --------------- 请求拦截器 ---------------
// 每次发请求前都会经过这里（适合加 token、打日志）
apiClient.interceptors.request.use(
  (config) => {
    // 浏览器控制台可见：发送请求: POST /api/trip/plan
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config // 必须返回 config，请求才会继续
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// --------------- 响应拦截器 ---------------
// 每次收到响应（或失败）都会经过这里
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    // 例如后端 500、网络断开、超时
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划（项目最核心的前端 API 函数）
 *
 * @param formData 旅行表单数据（城市、日期、偏好等）
 * @returns TripPlanResponse，其中 data 才是真正的 TripPlan
 *
 * 调用位置：Home.vue 的 handleSubmit
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    // 泛型 <TripPlanResponse>：告诉 TS，response.data 的类型是什么
    // 注意：axios 的 response.data = 后端返回的整个 JSON 包
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    // 优先取 FastAPI HTTPException 的 detail 字段，方便前端 message.error 展示
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

/**
 * 生成旅行计划（SSE 流式推流版）
 * 实时接收智能体节点的真实执行进度，避免前端假等待
 */
export async function generateTripPlanStream(
  formData: TripFormData,
  onProgress: (msg: string) => void
): Promise<TripPlanResponse> {
  const response = await fetch(`${API_BASE_URL}/api/trip/plan/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(formData)
  })

  if (!response.ok) {
    throw new Error(`HTTP 请求失败: ${response.status}`)
  }

  const reader = response.body?.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalPlan: any = null

  if (reader) {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() || '' // 留存未接收完整的尾部碎片

      for (const frame of frames) {
        if (!frame.trim()) continue
        const dataMatch = frame.match(/data:\s*(.+)$/s)
        if (dataMatch) {
          try {
            const payload = JSON.parse(dataMatch[1].trim())
            if (payload.message) {
              onProgress(payload.message)
            }
            if (payload.type === 'complete' && payload.data) {
              finalPlan = payload.data
              if (payload.trace_id && !finalPlan.trace_id) {
                finalPlan.trace_id = payload.trace_id
              }
            }
          } catch (e) {
            console.error('解析 SSE 数据帧异常:', e)
          }
        }
      }
    }
  }

  return {
    success: !!finalPlan,
    message: finalPlan ? '旅行计划生成成功' : '未能产出完整计划',
    data: finalPlan,
    trace_id: finalPlan?.trace_id
  }
}

/**
 * 健康检查
 * 对应后端 main.py 的 GET /health
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

// 默认导出 axios 实例，其他地方若要扩展接口可复用
export default apiClient
