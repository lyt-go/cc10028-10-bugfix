"""批次操作接口：批次归属校验、质检证据集、待核项与复核派单的专用入口。

与通用资源路由并列注册（且先于动态 ``/api/{module}`` 匹配），
所有业务判定都在领域服务内完成，路由层只做参数转发。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.annot_review import AnnotReviewService
from app.services.batch_attribution import BatchAttributionService
from app.services.extract_quality import ExtractQualityService

router = APIRouter(prefix="/api/batches", tags=["批次归属与质检"])


class QualityCheckPayload(BaseModel):
    sample_ratio: float = 0.1


class ReviewDispatchPayload(BaseModel):
    threshold: float = 0.80


class ClaimPayload(BaseModel):
    group: str


class ConclusionPayload(BaseModel):
    conclusion: str
    reviewer: str
    group: str = ""


class ResolvePendingPayload(BaseModel):
    batch_key: str
    operator: str = ""


def _unwrap(result: dict[str, Any]) -> dict[str, Any]:
    """服务返回 error 时以 400 暴露，保证前端与复核入口能读到失败原因。"""
    if "error" in result:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/unresolved/resolve")
def resolve_pending(payload: ResolvePendingPayload,
                    artifact_file: str, record_id: str) -> dict[str, Any]:
    """人工确认待核记录的真实批次，补齐处理来源登记。"""
    return _unwrap(BatchAttributionService().resolve_pending(
        artifact_file, record_id, payload.batch_key, payload.operator))


@router.get("/unresolved/pending")
def unresolved_pending() -> dict[str, Any]:
    """待核清单：现有字段无法判定来源的历史产物，不随任何批次重算入账。"""
    items = BatchAttributionService().list_unresolved()
    return {"total": len(items), "items": items}


@router.post("/{batch_key}/quality-check")
def quality_check(batch_key: str, payload: QualityCheckPayload) -> dict[str, Any]:
    """重算批次质检：归属校验 → 生成证据集 → 出具报告（幂等覆盖）。"""
    return _unwrap(
        ExtractQualityService().check_from_batch(batch_key, payload.sample_ratio))


@router.get("/{batch_key}/quality-evidence")
def quality_evidence(batch_key: str) -> dict[str, Any]:
    """查看批次质检证据集明细，详情里的每条证据都带归属依据。"""
    attribution = BatchAttributionService()
    items = attribution.load_batch_evidence(batch_key)
    return {"batch_key": batch_key, "total": len(items), "items": items}


@router.post("/{batch_key}/review-dispatch")
def review_dispatch(batch_key: str, payload: ReviewDispatchPayload) -> dict[str, Any]:
    """根据批次质检证据集提交复核派单请求，按来源负责组分流低置信度单据。"""
    return _unwrap(
        AnnotReviewService().create_review_tasks(batch_key, payload.threshold))


@router.get("/{batch_key}/review-tasks")
def review_tasks(batch_key: str) -> dict[str, Any]:
    """查看批次复核单，单据标明抽到了哪个批次的哪个实体或关系。"""
    items = AnnotReviewService().list_batch_tasks(batch_key)
    return {"batch_key": batch_key, "total": len(items), "items": items}


@router.post("/reviews/{review_id}/claim")
def claim_review(review_id: str, payload: ClaimPayload) -> dict[str, Any]:
    """对应来源负责组领取复核单，跨组/跨批领取会被归属权限拒绝。"""
    return _unwrap(AnnotReviewService().claim_review_task(review_id, payload.group))


@router.post("/reviews/{review_id}/conclusion")
def review_conclusion(review_id: str, payload: ConclusionPayload) -> dict[str, Any]:
    """负责组登记复核结论并结案。"""
    return _unwrap(AnnotReviewService().submit_review_conclusion(
        review_id, payload.conclusion, payload.reviewer, payload.group))
