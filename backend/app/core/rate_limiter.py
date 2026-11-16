"""
并发控制与限流重试模块 (Concurrency Limiter & Resilience)
================================================================================
后端工程核心基础设施：
1. 信号量限流 (asyncio.Semaphore)：限制下游大模型 (LLM) 与高德 API 的最大并发数，
   避免突发流量击垮第三方接口或触发 HTTP 429 Too Many Requests；
2. 指数退避重试 (Exponential Backoff with Jitter)：针对偶发超时与限流异常自动重试；
3. 运行态指标统计：暴露当前活跃并发数、排队等待数与重试总次数。
================================================================================
"""
import asyncio
import random
import time
from typing import Callable, Any
from loguru import logger


class ConcurrencyRateLimiter:
    """基于信号量与指数退避的 API 限流与重试保护器"""
    def __init__(self, max_concurrent: int = 5, max_retries: int = 3, base_delay: float = 1.0):
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # 统计指标
        self.active_requests = 0
        self.total_executed = 0
        self.total_retries = 0
        self.total_throttled = 0

    async def execute_async(self, func: Callable, *args, **kwargs) -> Any:
        """在并发信号量保护下异步执行函数，支持自动指数退避重试"""
        async with self._semaphore:
            self.active_requests += 1
            retries = 0
            try:
                while True:
                    try:
                        self.total_executed += 1
                        if asyncio.iscoroutinefunction(func):
                            return await func(*args, **kwargs)
                        else:
                            return await asyncio.to_thread(func, *args, **kwargs)
                    except Exception as e:
                        retries += 1
                        if retries > self.max_retries:
                            logger.error(f"[RateLimiter 熔断] 达到最大重试次数 ({self.max_retries})，抛出异常: {e}")
                            raise e

                        # 计算带抖动 (Full Jitter) 的退避时间，防止惊群效应 (Thundering Herd)
                        delay = self.base_delay * (2 ** (retries - 1)) + random.uniform(0.1, 0.5)
                        self.total_retries += 1
                        self.total_throttled += 1
                        logger.warning(
                            f"[RateLimiter 削峰] 捕获异常 [{e.__class__.__name__}: {e}]，"
                            f"第 {retries}/{self.max_retries} 次重试，退避等待 {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
            finally:
                self.active_requests -= 1

    def get_metrics(self) -> dict:
        """获取当前限流器指标"""
        return {
            "max_concurrent": self.max_concurrent,
            "active_requests": self.active_requests,
            "available_permits": self._semaphore._value,
            "total_executed": self.total_executed,
            "total_retries": self.total_retries,
            "total_throttled": self.total_throttled
        }


# 全局单例：限制最大 5 个下游高开销任务并发执行
llm_rate_limiter = ConcurrencyRateLimiter(max_concurrent=5, max_retries=3, base_delay=1.0)
