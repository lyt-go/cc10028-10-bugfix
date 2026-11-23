"""抽取质量服务：基于批次质检证据集的抽样质检、错抽漏抽统计与准确率结论。

归属修复后，本服务不再扫描 entities/relations 目录下的全局候选，而是：

1. 先由 ``BatchAttributionService`` 完成实体/关系源记录的批次归属校验，
   生成（或幂等重建）批次质检证据集；
2. 只对证据集中归属到本批次的记录抽样质检，同名实体/关系不会跨批混入；
3. 无法判定来源的历史产物只计入待核清单，重算报告时不进入当前批次统计。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.batch_attribution import BatchAttributionService
from app.store import store

# 低于该置信度的抽检记录判为错抽；实体与关系使用不同阈值
ENTITY_ERROR_THRESHOLD = 0.85
RELATION_ERROR_THRESHOLD = 0.80


class ExtractQualityService(BaseModuleService):
    MODULE = "extract_quality"
    ENTITY = "质检报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编号", "批次编号", "抽样比例", "抽检条目", "错抽数量", "漏抽数量",
                   "准确率", "待核项数量", "证据集文件", "质检结论"]
    REQUIRED_FIELDS = ["报告编号", "批次编号", "抽样比例"]
    STATUS_ORDER = ["草稿", "待质检", "质检中", "已出报告"]
    ACTION_RULES = {"启动质检": "待质检", "出具报告": "已出报告", "标记驳回": "已出报告"}
    NEGATIVE_ACTIONS = []

    def check_from_batch(self, batch_key: str, sample_ratio: float = 0.1) -> dict[str, Any]:
        """对指定批次执行归属校验并基于批次质检证据集抽样质检，生成质检报告。"""
        attribution = BatchAttributionService()

        # 第一步：归属校验，生成/重建批次证据集并登记待核历史产物
        evidence_result = attribution.build_batch_evidence(batch_key)
        if "error" in evidence_result:
            return evidence_result

        evidence = attribution.load_batch_evidence(batch_key)
        entity_evidence = [e for e in evidence if e["artifact_type"] == "entity"]
        relation_evidence = [e for e in evidence if e["artifact_type"] == "relation"]
        unresolved = attribution.list_unresolved()
        pending_count = len(unresolved)

        # 第二步：确定性等距抽样（同一份证据集重算结果稳定，不依赖全局候选顺序）
        sampled_entities = self._sampled(entity_evidence, sample_ratio)
        sampled_relations = self._sampled(relation_evidence, sample_ratio)
        sampled = sampled_entities + sampled_relations

        # 第三步：按置信度判定错抽；漏抽以本批内孤立关系计数（不跨批找实体）
        misjudged = 0
        for item in sampled:
            threshold = (ENTITY_ERROR_THRESHOLD if item["artifact_type"] == "entity"
                         else RELATION_ERROR_THRESHOLD)
            try:
                confidence = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            if confidence < threshold:
                misjudged += 1

        batch_entity_texts = {e["raw"].get("entity_text", "") for e in entity_evidence}
        missed_relations = 0
        for item in relation_evidence:
            rel = item["raw"]
            if (rel.get("head_entity", "") not in batch_entity_texts
                    or rel.get("tail_entity", "") not in batch_entity_texts):
                missed_relations += 1

        total_sampled = len(sampled)
        correct = total_sampled - misjudged
        accuracy = (correct / total_sampled) if total_sampled else 0.0

        if total_sampled == 0:
            conclusion = (f"本批无已归属抽取证据，{pending_count} 条历史待核项不计入当前批次；"
                          "待来源补齐后再行质检")
        elif accuracy >= 0.95:
            conclusion = "质检合格，准确率达到优秀水平"
        elif accuracy >= 0.85:
            conclusion = "质检通过，准确率达标，建议关注低置信度实体"
        elif accuracy >= 0.70:
            conclusion = "质检警告，准确率偏低需人工复核"
        else:
            conclusion = "质检不合格，准确率未达阈值需重新抽取"

        evidence_file = str(self._data_dir("quality_evidence") / f"{batch_key}_evidence.jsonl")
        report = {
            "报告编号": f"QC-{batch_key}",
            "批次编号": batch_key,
            "抽样比例": str(sample_ratio),
            "抽检条目": str(total_sampled),
            "错抽数量": str(misjudged),
            "漏抽数量": str(missed_relations),
            "准确率": f"{accuracy:.2%}" if total_sampled else "0.00%",
            "待核项数量": str(pending_count),
            "证据集文件": evidence_file,
            "质检结论": conclusion,
            "status": "已出报告",
        }

        # 报告文件随重算整体覆盖，历史待核数据不会被追加进当前批次
        output_path = self._data_dir("quality") / f"{batch_key}_quality.jsonl"
        self._overwrite_jsonl(output_path, [report])

        self._write_checkpoint(f"quality_{batch_key}", {
            "batch_key": batch_key,
            "sample_ratio": sample_ratio,
            "evidence_records": len(evidence),
            "sampled_items": total_sampled,
            "misjudged": misjudged,
            "missed_relations": missed_relations,
            "unresolved_pending": pending_count,
            "accuracy": accuracy,
            "conclusion": conclusion,
        })

        # 同步到工作台列表（同报告编号幂等更新，重算不产生重复行）
        self._upsert_store_report(report)

        return {
            "batch_key": batch_key,
            "report": report,
            "accuracy": accuracy,
            "evidence_records": len(evidence),
            "sampled_entities": len(sampled_entities),
            "sampled_relations": len(sampled_relations),
            "unresolved_pending": pending_count,
            "unresolved_items": unresolved,
            "output": str(output_path),
        }

    def cross_validate_entities(self, batch_key: str) -> dict[str, Any]:
        """在批次证据集范围内交叉校验实体与关系一致性，不跨批读取实体。"""
        attribution = BatchAttributionService()
        evidence = attribution.load_batch_evidence(batch_key)
        if not evidence:
            built = attribution.build_batch_evidence(batch_key)
            if "error" in built:
                return built
            evidence = attribution.load_batch_evidence(batch_key)

        entity_texts = {
            e["raw"].get("entity_text", "")
            for e in evidence if e["artifact_type"] == "entity"
        }
        orphan_relations = []
        for item in evidence:
            if item["artifact_type"] != "relation":
                continue
            rel = item["raw"]
            if (rel.get("head_entity", "") not in entity_texts
                    or rel.get("tail_entity", "") not in entity_texts):
                orphan_relations.append(item["record_id"])

        return {
            "batch_key": batch_key,
            "unique_entities": len(entity_texts),
            "orphan_relations": len(orphan_relations),
            "orphan_relation_ids": orphan_relations,
            "verdict": "一致" if not orphan_relations else f"存在{len(orphan_relations)}条孤立关系",
        }

    @staticmethod
    def _sampled(records: list[dict[str, Any]], ratio: float) -> list[dict[str, Any]]:
        """等距抽样：按证据集固定顺序抽取，保证重算可复现；非空集合至少抽1条。"""
        if not records:
            return []
        size = max(1, int(len(records) * ratio))
        size = min(size, len(records))
        if size == len(records):
            return list(records)
        stride = len(records) / size
        return [records[int(i * stride)] for i in range(size)]

    def _upsert_store_report(self, report: dict[str, Any]) -> None:
        rows = store.rows(self.MODULE)
        existing = next((r for r in rows if r.get("报告编号") == report["报告编号"]), None)
        if existing is not None:
            existing.update({k: v for k, v in report.items() if k != "id"})
            existing["pending"] = False
            return
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({field: report.get(field, "") for field in self.LIST_FIELDS})
        entry["status"] = "已出报告"
        entry["pending"] = False
        entry["abnormal"] = False
        rows.append(entry)
