"""POI相关API路由"""

import os
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from ...config import get_settings
from ...services.amap_service import get_amap_service
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/poi", tags=["POI"])


class POIDetailResponse(BaseModel):
    """POI详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片"
)
async def get_poi_detail(poi_id: str):
    """
    获取POI详情
    
    Args:
        poi_id: POI ID
        
    Returns:
        POI详情响应
    """
    try:
        amap_service = get_amap_service()
        
        # 调用高德地图POI详情API
        result = amap_service.get_poi_detail(poi_id)
        
        return POIDetailResponse(
            success=True,
            message="获取POI详情成功",
            data=result
        )
        
    except Exception as e:
        print(f"❌ 获取POI详情失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取POI详情失败: {str(e)}"
        )


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI"
)
async def search_poi(keywords: str, city: str = "北京"):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    try:
        amap_service = get_amap_service()
        result = amap_service.search_poi(keywords, city)

        return {
            "success": True,
            "message": "搜索成功",
            "data": result
        }

    except Exception as e:
        print(f"❌ 搜索POI失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"搜索POI失败: {str(e)}"
        )


@router.get(
    "/photo",
    summary="获取景点图片",
    description="优先从高德真实实拍图库获取，若无则降级从Unsplash获取图片"
)
async def get_attraction_photo(name: str):
    """
    获取景点图片

    Args:
        name: 景点名称

    Returns:
        图片URL
    """
    try:
        # 1. 优先从高德地图检索真实景点实拍照片 (精准度极高)
        settings = get_settings()
        amap_key = settings.amap_api_key or os.getenv("AMAP_API_KEY", "")
        if amap_key:
            try:
                amap_url = f"https://restapi.amap.com/v3/place/text?keywords={name}&key={amap_key}&offset=1&page=1"
                resp = requests.get(amap_url, timeout=3).json()
                if resp.get("status") == "1" and resp.get("pois"):
                    poi = resp["pois"][0]
                    photos = poi.get("photos", [])
                    if photos and photos[0].get("url"):
                        real_photo = photos[0]["url"]
                        return {
                            "success": True,
                            "message": "获取高德真实景点实拍照片成功",
                            "data": {
                                "name": name,
                                "photo_url": real_photo
                            }
                        }
            except Exception as e:
                print(f"⚠️ 从高德获取景点照片失败，降级至Unsplash: {e}")

        # 2. 降级备用：从 Unsplash 获取
        unsplash_service = get_unsplash_service()
        photo_url = unsplash_service.get_photo_url(f"{name} China") or unsplash_service.get_photo_url(name)

        return {
            "success": True,
            "message": "获取图片成功",
            "data": {
                "name": name,
                "photo_url": photo_url
            }
        }

    except Exception as e:
        print(f"❌ 获取景点图片失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取景点图片失败: {str(e)}"
        )

