"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import batch_ops, resource

# 批次专用路由必须先注册，避免被通用动态路由 /api/{module} 抢先匹配。
ROUTERS = [batch_ops, resource]
