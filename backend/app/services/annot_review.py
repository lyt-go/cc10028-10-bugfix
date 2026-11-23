"""人工复核服务：基于批次质检证据集的低置信度复核派单、负责组领取与结论登记。

归属修复后，复核任务不再扫描全局实体/关系候选目录，而是严格消费
``BatchAttributionService`` 生成的批次质检证据集：

- 每张复核单都带批次编号、文档编号、产物文件与归属证据，能指出
  抽到了哪个批次的哪个实体或关系；
- 派单请求按来源负责组分流，只有对应负责组能领取本类型低置信度复核单；
- 同名实体/关系不会让别批记录进入派单，待核历史产物不产生复核单。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.services.batch_attribution import BatchAttributionService
from app.store import store

# 复核对象类型 → 来源负责组：派单按此分流，领取时做归属权限校验
RESPONSIBLE_GROUPS = {
    "entity": "命名实体识别组",
    "relation": "关系抽取组",
}


class AnnotReviewService(BaseModuleService):
    MODULE = "annot_review"
    ENTITY = "复核任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["复核编号", "批次编号", "分块编号", "复核对象", "原判定", "来源负责组",
                   "证据归属", "复核结论", "复核人", "复核时间", "退回原因"]
    REQUIRED_FIELDS = ["复核编号", "批次编号", "分块编号", "复核对象"]
    STATUS_ORDER = ["草稿", "待复核", "复核中", "已结案"]
    ACTION_RULES = {"分派复核": "待复核", "提交结论": "已结案", "标记退回": "已结案"}
    NEGATIVE_ACTIONS = []

    def create_review_tasks(self, batch_key: str, threshold: float = 0.80) -> dict[str, Any]:
        """根据批次质检证据集筛选低置信度条目，提交复核派单请求并生成复核单。"""
        attribution = BatchAttributionService()
        evidence = attribution.load_batch_evidence(batch_key)
        if not evidence:
            # 证据集不存在时先构建归属证据；仍为空则拒绝凭全局候选派单
            built = attribution.build_batch_evidence(batch_key)
            if "error" in built:
                return built
            evidence = attribution.load_batch_evidence(batch_key)
        if not evidence:
            return {"error": f"批次 {batch_key} 无已归属质检证据，拒绝基于全局候选派单；"
                             f"待核历史产物 {len(attribution.list_unresolved())} 条"}

        tasks: list[dict[str, Any]] = []
        counter = 0
        for item in evidence:
            try:
                confidence = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            if confidence >= threshold:
                continue
            counter += 1
            artifact_type = item["artifact_type"]
            raw = item["raw"]
            group = RESPONSIBLE_GROUPS[artifact_type]
            if artifact_type == "entity":
                target = f"实体: {raw.get('entity_text')} [{raw.get('entity_type')}]"
                chunk_id = raw.get("chunk_id", "")
            else:
                target = (f"关系: {raw.get('head_entity')}→{raw.get('relation_type')}"
                          f"→{raw.get('tail_entity')}")
                chunk_id = ""
            tasks.append({
                "复核编号": f"REV-{batch_key}-{counter:06d}",
                "批次编号": batch_key,
                "文档编号": item.get("doc_id", ""),
                "分块编号": chunk_id,
                "复核对象": target,
                "对象类型": artifact_type,
                "记录编号": item.get("record_id", ""),
                "产物文件": item.get("artifact_file", ""),
                "原判定": f"置信度 {confidence:.2f}",
                "来源负责组": group,
                "来源系统": self._source_system_of(batch_key),
                "证据归属": f"证据等级{item['evidence_level']}：{item['evidence_reason']}",
                "复核结论": "",
                "复核人": "",
                "复核时间": "",
                "领取组": "",
                "退回原因": "",
                "status": "待复核",
            })

        # 复核单按批次覆盖重写，重派不产生重复单据
        output_path = self._data_dir("reviews") / f"{batch_key}_reviews.jsonl"
        written = self._overwrite_jsonl(output_path, tasks)

        # 派单请求留痕：记录是基于哪份证据集、向哪些负责组派单
        dispatch = {
            "batch_key": batch_key,
            "threshold": threshold,
            "tasks_created": len(tasks),
            "groups": sorted({t["来源负责组"] for t in tasks}),
            "entity_tasks": sum(1 for t in tasks if t["对象类型"] == "entity"),
            "relation_tasks": sum(1 for t in tasks if t["对象类型"] == "relation"),
            "evidence_file": str(self._data_dir("quality_evidence") / f"{batch_key}_evidence.jsonl"),
            "dispatched_at": time.time(),
        }
        self._write_jsonl(self._data_dir("reviews") / "dispatch_requests.jsonl", [dispatch])

        self._write_checkpoint(f"review_{batch_key}", dispatch)

        return {
            "batch_key": batch_key,
            "threshold": threshold,
            "tasks_created": len(tasks),
            "records_written": written,
            "groups": dispatch["groups"],
            "output": str(output_path),
        }

    def claim_review_task(self, review_id: str, group: str) -> dict[str, Any]:
        """来源负责组领取复核单；非对应负责组或跨批领取一律拒绝。"""
        for task_file, tasks, task in self._iter_tasks():
            if task.get("复核编号") != review_id:
                continue
            if task.get("status") != "待复核":
                return {"error": f"复核单 {review_id} 当前状态 {task.get('status')}，不可领取"}
            if task.get("来源负责组") != group:
                # 归属权限：跨组/跨批领取被拦截，同名证据不会被别组拿走
                return {"error": f"领取被拒：复核单 {review_id} 归属 "
                                 f"{task.get('来源负责组')}（批次 {task.get('批次编号')}），"
                                 f"{group} 无权领取"}
            task["status"] = "复核中"
            task["领取组"] = group
            task["领取时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._overwrite_jsonl(task_file, tasks)
            return {"review_id": review_id, "group": group,
                    "batch_key": task.get("批次编号"), "status": "复核中"}
        return {"error": f"复核任务 {review_id} 不存在"}

    def submit_review_conclusion(self, review_id: str, conclusion: str,
                                 reviewer: str, group: str = "") -> dict[str, Any]:
        """提交复核结论并更新任务状态；可选校验提交组与领取组一致。"""
        for task_file, tasks, task in self._iter_tasks():
            if task.get("复核编号") != review_id:
                continue
            if group and task.get("领取组") and group != task.get("领取组"):
                return {"error": f"提交被拒：复核单 {review_id} 由 {task.get('领取组')} 领取，"
                                 f"{group} 无权结案"}
            task["复核结论"] = conclusion
            task["复核人"] = reviewer
            task["复核时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
            task["status"] = "已结案"
            self._overwrite_jsonl(task_file, tasks)
            return {"review_id": review_id, "conclusion": conclusion,
                    "reviewer": reviewer, "batch_key": task.get("批次编号"),
                    "status": "已结案"}
        return {"error": f"复核任务 {review_id} 不存在"}

    def list_batch_tasks(self, batch_key: str) -> list[dict[str, Any]]:
        """读取指定批次的复核单，供工作台展示派单明细。"""
        return self._read_jsonl(
            self._data_dir("reviews") / f"{batch_key}_reviews.jsonl")

    # ------------------------------------------------------------------

    def _iter_tasks(self):
        """遍历全部复核文件，产出(文件路径, 文件内任务列表, 单个任务)。

        任务列表保留在内存中，调用方修改后整体覆盖回写，避免先清空后
        重写造成的丢单窗口。
        """
        for rf in sorted(self._data_dir("reviews").glob("*_reviews.jsonl")):
            tasks = self._read_jsonl(rf)
            for task in tasks:
                yield rf, tasks, task

    @staticmethod
    def _source_system_of(batch_key: str) -> str:
        for r in store.rows("file_intake"):
            if r.get("批次编号") == batch_key:
                return str(r.get("来源系统", ""))
        return ""
