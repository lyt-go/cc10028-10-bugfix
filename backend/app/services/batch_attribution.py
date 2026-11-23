"""批次归属服务：实体/关系源记录的批次归属校验与批次质检证据集管理。

背景
----
历史实现里，质检报告与人工复核入口直接扫描 entities/relations 目录下的
全部全局候选文件，导致 A 批的同名实体会被计入 B 批，详情证据归属对不上。

本模块是「批次归属」的唯一权威入口，所有归属判定只能使用可核对的证据：

1. 记录内显式 ``batch_key``（最强证据，记录自身声明来源）；
2. 处理来源登记 ``data/provenance/artifact_sources.jsonl``
   （哪个批次接入的哪个文档产出了哪个产物文件，含登记时间与来源系统）；
3. 批次接入清单（data/batches/onboard.jsonl + file_intake 内存表），
   按文档编号匹配，兼容不同批次存在同名文件的情况；
4. 关系文件血缘：仅文档编号可辨时，等待后续关系文件携带的处理来源。

无法用上述证据确认来源的历史产物一律登记为「待核」
（data/provenance/unresolved.jsonl），不允许按实体名/文件名猜测归属，
也不允许跨批读取绕过归属权限。重算报告时待核记录不会被计入任何当前批次。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store

# 归属证据等级：数字越小越可信，仅用于登记证据来源，不改变判定规则
EVIDENCE_RECORD_BATCH = 1   # 记录内显式批次字段
EVIDENCE_PROVENANCE = 2     # 处理来源登记（产物级）
EVIDENCE_INTAKE_LIST = 3    # 批次接入清单按文档编号匹配
EVIDENCE_RELATION_BLOOD = 4  # 关系文件血缘（预留：后续关系文件补齐来源）

UNRESOLVED_BATCH = "待核"
PENDING_STATUS = "待核"


class BatchAttributionService(BaseModuleService):
    """批次归属校验与证据集服务，无独立业务表，只产出 provenance 数据。"""

    MODULE = "batch_attribution"
    ENTITY = "批次归属证据"

    # ------------------------------------------------------------------
    # 处理来源登记
    # ------------------------------------------------------------------

    def register_artifact_source(
        self,
        batch_key: str,
        doc_id: str,
        artifact_file: str,
        artifact_type: str,
        source_system: str = "",
    ) -> dict[str, Any]:
        """登记某批次接入文档与抽取产物文件之间的处理来源关系。

        幂等：同一 artifact_file 已登记为同一批次时直接复用；
        已登记为其他批次时拒绝覆盖，防止同名文件串批。
        """
        registry = self._load_registry()
        for item in registry:
            if item.get("artifact_file") == artifact_file:
                if item.get("batch_key") == batch_key:
                    return {"artifact_file": artifact_file, "batch_key": batch_key,
                            "registered": False, "reason": "已存在同源登记"}
                return {"error": f"产物 {artifact_file} 已登记给批次 "
                                 f"{item.get('batch_key')}，禁止改挂到 {batch_key}"}

        entry = {
            "batch_key": batch_key,
            "doc_id": doc_id,
            "artifact_file": artifact_file,
            "artifact_type": artifact_type,
            "source_system": source_system or self._source_system_of(batch_key),
            "evidence_level": EVIDENCE_PROVENANCE,
            "registered_at": time.time(),
        }
        self._write_jsonl(self._registry_path(), [entry])
        return {"artifact_file": artifact_file, "batch_key": batch_key,
                "registered": True, "entry": entry}

    def list_artifact_sources(self) -> list[dict[str, Any]]:
        """读取全部处理来源登记，供质检与复核核对证据。"""
        return self._load_registry()

    # ------------------------------------------------------------------
    # 归属判定
    # ------------------------------------------------------------------

    def resolve_record_batch(
        self, record: dict[str, Any], artifact_file: str, artifact_type: str
    ) -> dict[str, Any]:
        """按证据优先级判定单条实体/关系记录的批次归属。

        返回 ``{batch_key, status, evidence_level, reason, doc_id}``。
        任何证据都无法唯一确认来源时返回待核结论，绝不按名称猜测。
        """
        # 证据1：记录内显式批次字段（当前管线产物自带）
        explicit = str(record.get("batch_key", "")).strip()
        if explicit:
            return {
                "batch_key": explicit,
                "status": "已归属",
                "evidence_level": EVIDENCE_RECORD_BATCH,
                "reason": "记录内显式batch_key",
                "doc_id": record.get("doc_id", ""),
            }

        # 证据2：处理来源登记（产物文件级）
        registry = self._load_registry()
        provenance = [p for p in registry if p.get("artifact_file") == artifact_file]
        if provenance:
            batch_keys = {p.get("batch_key") for p in provenance}
            if len(batch_keys) == 1:
                p = provenance[0]
                return {
                    "batch_key": p["batch_key"],
                    "status": "已归属",
                    "evidence_level": EVIDENCE_PROVENANCE,
                    "reason": f"处理来源登记（{p.get('source_system', '')}）",
                    "doc_id": p.get("doc_id", ""),
                }
            return self._unresolved(
                artifact_file, artifact_type, record,
                reason=f"处理来源登记指向多个批次：{sorted(batch_keys)}",
                doc_id=provenance[0].get("doc_id", ""),
            )

        # 证据3：批次接入清单按文档编号匹配
        doc_id = self._doc_id_of(record, artifact_file)
        if doc_id:
            owners = self._batches_owning_doc(doc_id)
            if len(owners) == 1:
                return {
                    "batch_key": owners[0]["batch_key"],
                    "status": "已归属",
                    "evidence_level": EVIDENCE_INTAKE_LIST,
                    "reason": f"接入清单文档编号匹配（{doc_id}）",
                    "doc_id": doc_id,
                }
            if len(owners) > 1:
                return self._unresolved(
                    artifact_file, artifact_type, record,
                    reason=f"文档 {doc_id} 同时出现在多个批次清单，"
                           f"同名文件无法仅凭文档编号定批：{[o['batch_key'] for o in owners]}",
                    doc_id=doc_id,
                )

        # 证据4（预留）：关系文件血缘补齐来源——当前无后续关系文件可核对
        return self._unresolved(
            artifact_file, artifact_type, record,
            reason="记录缺少批次字段且无处理来源登记/接入清单可核对，"
                   "等待后续关系文件补齐处理来源",
            doc_id=doc_id,
        )

    def build_batch_evidence(self, batch_key: str) -> dict[str, Any]:
        """扫描全部实体/关系源记录做归属校验，生成该批次的质检证据集。

        - 归属到本批次的记录写入 ``data/quality_evidence/{batch}_evidence.jsonl``；
        - 无法判定来源的历史产物登记到待核清单（去重，不随重算重复写入）；
        - 归属到其他批次的记录既不进入本批证据集，也不进入待核清单，
          同名实体不能让别批记录进来。
        """
        if not self._batch_known(batch_key):
            return {"error": f"批次 {batch_key} 未在文件接入登记，拒绝为未知批次构建证据集"}

        owned: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        scanned_files: list[str] = []

        for artifact_type, sub in (("entity", "entities"), ("relation", "relations")):
            for path in sorted(self._data_dir(sub).glob("*.jsonl")):
                artifact_file = path.name
                scanned_files.append(artifact_file)
                for record in self._read_jsonl(path):
                    decision = self.resolve_record_batch(record, artifact_file, artifact_type)
                    record_id = self._record_id(record, artifact_type)
                    if decision["status"] == PENDING_STATUS:
                        unresolved.append(self._make_unresolved_entry(
                            artifact_file, artifact_type, record_id, record, decision))
                        continue
                    if decision["batch_key"] != batch_key:
                        # 归属明确但属于别的批次：跨批读取被归属权限拦截
                        continue
                    owned.append(self._make_evidence_entry(
                        batch_key, artifact_file, artifact_type, record_id, record, decision))

        new_pending = self._merge_unresolved(unresolved)

        # 证据集随重算整体重写，保证只反映当前归属判定结果
        evidence_path = self._data_dir("quality_evidence") / f"{batch_key}_evidence.jsonl"
        self._overwrite_jsonl(evidence_path, owned)
        self._write_checkpoint(f"evidence_{batch_key}", {
            "batch_key": batch_key,
            "scanned_files": scanned_files,
            "evidence_records": len(owned),
            "unresolved_total": len(self._read_jsonl(self._unresolved_path())),
            "new_unresolved": new_pending,
            "rebuilt_at": time.time(),
        })

        return {
            "batch_key": batch_key,
            "evidence_records": len(owned),
            "entity_records": sum(1 for r in owned if r["artifact_type"] == "entity"),
            "relation_records": sum(1 for r in owned if r["artifact_type"] == "relation"),
            "new_unresolved": new_pending,
            "unresolved_total": len(self._read_jsonl(self._unresolved_path())),
            "scanned_files": scanned_files,
            "evidence_file": str(evidence_path),
        }

    def load_batch_evidence(self, batch_key: str) -> list[dict[str, Any]]:
        """读取批次质检证据集；文件不存在时调用方应先构建，不允许回退到全局候选。"""
        return self._read_jsonl(
            self._data_dir("quality_evidence") / f"{batch_key}_evidence.jsonl")

    def assert_batch_access(self, batch_key: str, artifact_file: str) -> None:
        """归属权限守卫：跨批读取产物文件时直接拒绝，不能绕过归属权限。"""
        if not self._batch_known(batch_key):
            raise PermissionError(f"归属权限拒绝：批次 {batch_key} 未登记，禁止读取任何产物")
        registry = self._load_registry()
        for item in registry:
            if item.get("artifact_file") == artifact_file and item.get("batch_key") != batch_key:
                raise PermissionError(
                    f"归属权限拒绝：产物 {artifact_file} 属于批次 {item.get('batch_key')}，"
                    f"批次 {batch_key} 无权跨批读取")

    # ------------------------------------------------------------------
    # 待核清单
    # ------------------------------------------------------------------

    def list_unresolved(self) -> list[dict[str, Any]]:
        """读取全部待核历史产物记录。"""
        return self._read_jsonl(self._unresolved_path())

    def resolve_pending(self, artifact_file: str, record_id: str, batch_key: str,
                        operator: str = "") -> dict[str, Any]:
        """人工确认待核记录的真实批次：补登记处理来源并从待核清单移除。"""
        if not self._batch_known(batch_key):
            return {"error": f"批次 {batch_key} 未登记，不能作为确认归属"}
        pending = self._read_jsonl(self._unresolved_path())
        target = [r for r in pending
                  if r.get("artifact_file") == artifact_file and r.get("record_id") == record_id]
        if not target:
            return {"error": f"待核记录 {artifact_file}#{record_id} 不存在"}
        entry = target[0]
        self.register_artifact_source(
            batch_key, entry.get("doc_id", ""), artifact_file,
            entry.get("artifact_type", "entity"),
            source_system=f"人工确认:{operator}" if operator else "人工确认")
        remaining = [r for r in pending if not (
            r.get("artifact_file") == artifact_file and r.get("record_id") == record_id)]
        self._overwrite_jsonl(self._unresolved_path(), remaining)
        return {"artifact_file": artifact_file, "record_id": record_id,
                "batch_key": batch_key, "resolved": True}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _registry_path(self):
        return self._data_dir("provenance") / "artifact_sources.jsonl"

    def _unresolved_path(self):
        return self._data_dir("provenance") / "unresolved.jsonl"

    def _load_registry(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self._registry_path())

    def _batch_known(self, batch_key: str) -> bool:
        return any(r.get("批次编号") == batch_key for r in store.rows("file_intake"))

    def _source_system_of(self, batch_key: str) -> str:
        for r in store.rows("file_intake"):
            if r.get("批次编号") == batch_key:
                return str(r.get("来源系统", ""))
        return ""

    def _batches_owning_doc(self, doc_id: str) -> list[dict[str, Any]]:
        """返回接入清单中包含该文档编号的批次（不同批次可能有同名文件）。"""
        owners = []
        for row in store.rows("file_intake"):
            files = [f.strip() for f in str(row.get("文件清单", "")).split(",") if f.strip()]
            if doc_id in files or f"{doc_id}.pdf" in files or f"{doc_id}.jsonl" in files:
                owners.append({"batch_key": row.get("批次编号"),
                               "source_system": row.get("来源系统", "")})
        return owners

    @staticmethod
    def _doc_id_of(record: dict[str, Any], artifact_file: str) -> str:
        for key in ("doc_id", "source_doc"):
            if str(record.get(key, "")).strip():
                return str(record[key]).strip()
        # 老产物只剩文档编号：文件名形如 {doc_id}_entities.jsonl
        stem = artifact_file
        for suffix in ("_entities.jsonl", "_relations.jsonl", ".jsonl"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        stem = stem.removesuffix("_parsed")
        return stem

    @staticmethod
    def _record_id(record: dict[str, Any], artifact_type: str) -> str:
        if artifact_type == "entity":
            return str(record.get("entity_id") or record.get("实体编号") or "")
        return str(record.get("relation_id") or record.get("关系编号") or "")

    def _unresolved(self, artifact_file: str, artifact_type: str,
                    record: dict[str, Any], reason: str, doc_id: str = "") -> dict[str, Any]:
        return {
            "batch_key": UNRESOLVED_BATCH,
            "status": PENDING_STATUS,
            "evidence_level": None,
            "reason": reason,
            "doc_id": doc_id or self._doc_id_of(record, artifact_file),
        }

    def _make_evidence_entry(self, batch_key, artifact_file, artifact_type,
                             record_id, record, decision) -> dict[str, Any]:
        entry = {
            "batch_key": batch_key,
            "artifact_type": artifact_type,
            "artifact_file": artifact_file,
            "record_id": record_id,
            "evidence_level": decision["evidence_level"],
            "evidence_reason": decision["reason"],
            "doc_id": decision.get("doc_id", ""),
            "confidence": record.get("confidence", record.get("置信度", 1.0)),
            "raw": record,
        }
        if artifact_type == "entity":
            entry["target_text"] = record.get("entity_text", "")
            entry["target_type"] = record.get("entity_type", "")
        else:
            entry["target_text"] = (
                f"{record.get('head_entity', '')}→{record.get('relation_type', '')}"
                f"→{record.get('tail_entity', '')}")
            entry["target_type"] = record.get("relation_type", "")
        return entry

    def _make_unresolved_entry(self, artifact_file, artifact_type, record_id,
                               record, decision) -> dict[str, Any]:
        return {
            "artifact_file": artifact_file,
            "artifact_type": artifact_type,
            "record_id": record_id,
            "doc_id": decision.get("doc_id", ""),
            "target_text": record.get("entity_text")
            or f"{record.get('head_entity', '')}→{record.get('tail_entity', '')}",
            "reason": decision["reason"],
            "registered_at": time.time(),
        }

    def _merge_unresolved(self, found: Iterable[dict[str, Any]]) -> int:
        """按本次全量扫描结果核对待核清单，返回本次新增条数。

        扫描覆盖 entities/relations 下全部现存文件，因此：
        - 本次仍无法定批的记录保留/新增；
        - 已补齐证据可定批、或产物文件已删除的历史待核项自动剔除，
          避免待核清单残留过期数据。
        """
        current: dict[tuple[str, str], dict[str, Any]] = {}
        for item in found:
            current.setdefault((item["artifact_file"], item["record_id"]), item)
        existing = self._read_jsonl(self._unresolved_path())
        old_keys = {(r.get("artifact_file"), r.get("record_id")) for r in existing}
        added = len(set(current) - old_keys)
        if set(current) != old_keys:
            self._overwrite_jsonl(self._unresolved_path(), list(current.values()))
        return added
