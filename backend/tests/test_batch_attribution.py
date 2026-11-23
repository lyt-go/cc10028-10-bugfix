"""批次归属校验集成测试。

场景（与需求验收一一对应）：
- A 批、B 批各接入一份可辨来源，两份清单含同名机构「华信集团」；
- 另放一份只剩文档编号、无任何来源字段的老结果（未能归属的旧结果）；
- 验证质检报告、待核项与复核单合起来能说明三份输入分别去了哪里。
"""
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.services.annot_review import AnnotReviewService, RESPONSIBLE_GROUPS
from app.services.base import DATA_ROOT
from app.services.batch_attribution import (
    BatchAttributionService,
    EVIDENCE_RECORD_BATCH,
    EVIDENCE_PROVENANCE,
    EVIDENCE_INTAKE_LIST,
)
from app.services.extract_quality import ExtractQualityService
from app.services.file_intake import FileIntakeService

SAME_NAME_ORG = "华信集团"


def _onboard(svc, batch_key, source_system, files):
    return svc.onboard_batch({
        "批次编号": batch_key,
        "来源系统": source_system,
        "文件清单": ",".join(files),
        "文件类型": "PDF",
        "字节总量": "2048",
        "校验值": "",
        "采集方式": "接口",
        "接入游标": "0",
    })


def _entity(entity_id, doc_id, text, confidence, batch_key=""):
    rec = {
        "entity_id": entity_id,
        "doc_id": doc_id,
        "chunk_id": f"CHUNK-{doc_id}-0001",
        "entity_type": "ORG",
        "entity_text": text,
        "start_offset": 0,
        "end_offset": len(text),
        "confidence": confidence,
        "model": "NER-BiLSTM-CRF",
        "model_version": "v3.1.0",
    }
    if batch_key:
        rec["batch_key"] = batch_key
    return rec


def _relation(rel_id, doc_id, head, tail, confidence, batch_key=""):
    rec = {
        "relation_id": rel_id,
        "doc_id": doc_id,
        "head_entity": head,
        "head_type": "ORG",
        "tail_entity": tail,
        "tail_type": "PERSON",
        "relation_type": "EMPLOY",
        "confidence": confidence,
        "evidence_sentence": f"{head} 雇佣 {tail}",
        "method": "Relation-BERT",
        "model_version": "v2.3.0",
    }
    if batch_key:
        rec["batch_key"] = batch_key
    return rec


@pytest.fixture(autouse=True)
def isolate_attribution_data():
    """归属测试独占 provenance/evidence/quality/reviews 与实体关系产物目录。

    DATA_ROOT 是跨测试共享的持久化目录，其他链路测试留下的无主产物会被
    归属判定登记为待核；本夹具在每个用例前后清空相关子目录保证隔离。
    """
    touched = ("provenance", "quality_evidence", "quality", "reviews",
               "entities", "relations", "checkpoints")
    for sub in touched:
        target = DATA_ROOT / sub
        if target.exists():
            for fp in list(target.glob("*")):
                fp.unlink()
    yield
    for sub in touched:
        target = DATA_ROOT / sub
        if target.exists():
            for fp in list(target.glob("*")):
                fp.unlink()


@pytest.fixture()
def scenario():
    """构造 A/B/旧结果 三份输入，返回各产物文件名与服务实例。"""
    svc = BatchAttributionService()
    intake = FileIntakeService()

    batch_a = "BATCH-A-ATTRIB"
    batch_b = "BATCH-B-ATTRIB"
    doc_a = "DOCA-ATTRIB"
    doc_b = "DOCB-ATTRIB"
    doc_legacy = "DOC-LEGACY-OLD"

    _onboard(intake, batch_a, "来源系统A", [f"{doc_a}.pdf"])
    _onboard(intake, batch_b, "来源系统B", [f"{doc_b}.pdf"])

    # 输入1：A 批产物，记录自带 batch_key（当前管线形态，含同名机构）
    a_entities = [
        _entity("ENT-A-001", doc_a, SAME_NAME_ORG, 0.96, batch_key=batch_a),
        _entity("ENT-A-002", doc_a, "A批专属机构", 0.72, batch_key=batch_a),  # 低置信
    ]
    a_file = f"{doc_a}_parsed_entities.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "entities" / a_file, a_entities)
    a_rels = [_relation("REL-A-001", doc_a, SAME_NAME_ORG, "张三", 0.91, batch_key=batch_a)]
    a_rel_file = f"{doc_a}_parsed_relations.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "relations" / a_rel_file, a_rels)
    # A 批产物同时登记处理来源，供归属权限守卫识别文件归属
    svc.register_artifact_source(batch_a, doc_a, a_file, "entity", "来源系统A")
    svc.register_artifact_source(batch_a, doc_a, a_rel_file, "relation", "来源系统A")

    # 输入2：B 批产物，记录无 batch_key，仅靠处理来源登记定批（老管线产物形态）
    b_entities = [
        _entity("ENT-B-001", doc_b, SAME_NAME_ORG, 0.95),
        _entity("ENT-B-002", doc_b, "B批专属机构", 0.68),  # 低置信
    ]
    b_file = f"{doc_b}_parsed_entities.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "entities" / b_file, b_entities)
    b_rels = [_relation("REL-B-001", doc_b, SAME_NAME_ORG, "李四", 0.66)]  # 低置信关系
    b_rel_file = f"{doc_b}_parsed_relations.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "relations" / b_rel_file, b_rels)
    svc.register_artifact_source(batch_b, doc_b, b_file, "entity", "来源系统B")
    svc.register_artifact_source(batch_b, doc_b, b_rel_file, "relation", "来源系统B")

    # 输入3：未能归属的旧结果——只剩文档编号，无批次字段、无来源登记、不在接入清单
    legacy_entities = [
        _entity("ENT-LEG-001", doc_legacy, SAME_NAME_ORG, 0.70),
    ]
    legacy_file = f"{doc_legacy}_parsed_entities.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "entities" / legacy_file, legacy_entities)
    legacy_rels = [_relation("REL-LEG-001", doc_legacy, SAME_NAME_ORG, "王五", 0.61)]
    legacy_rel_file = f"{doc_legacy}_parsed_relations.jsonl"
    svc._overwrite_jsonl(DATA_ROOT / "relations" / legacy_rel_file, legacy_rels)

    return {
        "svc": svc,
        "batch_a": batch_a,
        "batch_b": batch_b,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "doc_legacy": doc_legacy,
        "a_file": a_file,
        "b_file": b_file,
        "legacy_file": legacy_file,
        "a_rel_file": a_rel_file,
        "b_rel_file": b_rel_file,
        "legacy_rel_file": legacy_rel_file,
    }


class TestBatchAttribution:
    def test_three_inputs_land_in_three_places(self, scenario):
        """A 证据集、B 证据集、待核清单各自只收到自己那份输入。"""
        svc = scenario["svc"]
        result_a = svc.build_batch_evidence(scenario["batch_a"])
        result_b = svc.build_batch_evidence(scenario["batch_b"])
        assert "error" not in result_a
        assert "error" not in result_b

        ev_a = svc.load_batch_evidence(scenario["batch_a"])
        ev_b = svc.load_batch_evidence(scenario["batch_b"])
        pending = svc.list_unresolved()

        # A 批：2 实体 + 1 关系，全部记录自带批次字段（证据等级1）
        assert len(ev_a) == 3
        assert {e["artifact_file"] for e in ev_a} == {
            scenario["a_file"], scenario["a_rel_file"]}
        assert all(e["evidence_level"] == EVIDENCE_RECORD_BATCH for e in ev_a)

        # B 批：2 实体 + 1 关系，靠处理来源登记定批（证据等级2）
        assert len(ev_b) == 3
        assert {e["artifact_file"] for e in ev_b} == {
            scenario["b_file"], scenario["b_rel_file"]}
        assert all(e["evidence_level"] == EVIDENCE_PROVENANCE for e in ev_b)

        # 旧结果：2 条记录全部待核，不属于任何批次
        pending_keys = {(p["artifact_file"], p["record_id"]) for p in pending}
        assert (scenario["legacy_file"], "ENT-LEG-001") in pending_keys
        assert (scenario["legacy_rel_file"], "REL-LEG-001") in pending_keys
        assert all(p["reason"] for p in pending)

    def test_same_name_org_does_not_cross_batch(self, scenario):
        """同名机构不能让别批记录进来：两份证据集都含华信集团但互不混入。"""
        svc = scenario["svc"]
        svc.build_batch_evidence(scenario["batch_a"])
        svc.build_batch_evidence(scenario["batch_b"])

        ev_a = svc.load_batch_evidence(scenario["batch_a"])
        ev_b = svc.load_batch_evidence(scenario["batch_b"])

        same_name_a = [e for e in ev_a
                       if SAME_NAME_ORG in e["target_text"] or e["raw"].get("head_entity") == SAME_NAME_ORG]
        same_name_b = [e for e in ev_b
                       if SAME_NAME_ORG in e["target_text"] or e["raw"].get("head_entity") == SAME_NAME_ORG]
        assert len(same_name_a) == 2  # A 批的同名实体与含同名机构的关系
        assert len(same_name_b) == 2
        # A 批证据里不允许出现 B 批/旧结果的产物文件
        assert scenario["b_file"] not in {e["artifact_file"] for e in ev_a}
        assert scenario["legacy_file"] not in {e["artifact_file"] for e in ev_a}
        assert scenario["a_file"] not in {e["artifact_file"] for e in ev_b}
        # 每条证据详情都带可核对的归属依据
        assert all(e["evidence_reason"] and e["batch_key"] for e in ev_a + ev_b)

    def test_recompute_keeps_legacy_out_of_current_batch(self, scenario):
        """再次打开报告重算时，历史待核数据不会被随手算进当前批。"""
        quality = ExtractQualityService()
        first = quality.check_from_batch(scenario["batch_b"], sample_ratio=1.0)
        second = quality.check_from_batch(scenario["batch_b"], sample_ratio=1.0)

        # 重算幂等：抽检条目始终只含 B 批 3 条已归属记录
        assert int(first["report"]["抽检条目"]) == 3
        assert int(second["report"]["抽检条目"]) == 3
        # 待核项数量稳定为旧结果的 2 条，不会因重算翻倍
        assert first["unresolved_pending"] == 2
        assert second["unresolved_pending"] == 2

        # 报告文件被覆盖而不是追加：文件内只有一行 B 批报告
        report_file = DATA_ROOT / "quality" / f"{scenario['batch_b']}_quality.jsonl"
        lines = [ln for ln in report_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_quality_report_excludes_pending_and_counts_b_only(self, scenario):
        """B 批报告只统计 B 批：低置信错抽可被识别，待核项单独列示。"""
        quality = ExtractQualityService()
        result = quality.check_from_batch(scenario["batch_b"], sample_ratio=1.0)
        report = result["report"]

        assert report["批次编号"] == scenario["batch_b"]
        assert int(report["抽检条目"]) == 3
        # B 批低置信：ENT-B-002(0.68) 与 REL-B-001(0.66) 判为错抽
        assert int(report["错抽数量"]) == 2
        assert int(report["待核项数量"]) == 2
        assert report["证据集文件"].endswith(f"{scenario['batch_b']}_evidence.jsonl")

    def test_review_dispatch_points_to_batch_and_record(self, scenario):
        """复核派单基于 B 批证据集，单据能指出批次与具体实体/关系。"""
        review = AnnotReviewService()
        dispatch = review.create_review_tasks(scenario["batch_b"], threshold=0.80)
        assert "error" not in dispatch
        # 低置信单据：ENT-B-002 与 REL-B-001
        assert dispatch["tasks_created"] == 2
        tasks = review.list_batch_tasks(scenario["batch_b"])
        assert len(tasks) == 2
        for task in tasks:
            assert task["批次编号"] == scenario["batch_b"]
            assert task["记录编号"]  # 能指出抽到了哪个实体或关系
            assert task["产物文件"] in {scenario["b_file"], scenario["b_rel_file"]}
            assert task["证据归属"].startswith("证据等级")
        groups = {t["来源负责组"] for t in tasks}
        assert groups == set(RESPONSIBLE_GROUPS.values())

    def test_review_claim_enforces_responsible_group(self, scenario):
        """只有对应来源负责组能领取；跨组/跨批领取被归属权限拒绝。"""
        review = AnnotReviewService()
        review.create_review_tasks(scenario["batch_b"], threshold=0.80)
        entity_task = next(
            t for t in review.list_batch_tasks(scenario["batch_b"])
            if t["对象类型"] == "entity")

        wrong = review.claim_review_task(entity_task["复核编号"], RESPONSIBLE_GROUPS["relation"])
        assert "error" in wrong and "无权领取" in wrong["error"]

        ok = review.claim_review_task(
            entity_task["复核编号"], RESPONSIBLE_GROUPS["entity"])
        assert ok["status"] == "复核中"
        assert ok["batch_key"] == scenario["batch_b"]

        # 结案同样校验领取组
        blocked = review.submit_review_conclusion(
            entity_task["复核编号"], "维持原判", "复核员乙",
            group=RESPONSIBLE_GROUPS["relation"])
        assert "error" in blocked
        closed = review.submit_review_conclusion(
            entity_task["复核编号"], "实体边界修正", "复核员甲",
            group=RESPONSIBLE_GROUPS["entity"])
        assert closed["status"] == "已结案"

    def test_dispatch_without_evidence_refuses_global_candidates(self, scenario):
        """未登记批次无法构建证据集，派单拒绝回退到全局候选目录。"""
        review = AnnotReviewService()
        result = review.create_review_tasks("BATCH-NEVER-ONBOARDED", threshold=0.99)
        assert "error" in result
        assert "未知批次" in result["error"] or "无已归属" in result["error"]

    def test_cross_batch_artifact_access_blocked(self, scenario):
        """跨批读取不能绕过归属权限：B 批读取 A 批产物文件直接拒绝。"""
        svc = scenario["svc"]
        with pytest.raises(PermissionError):
            svc.assert_batch_access(scenario["batch_b"], scenario["a_file"])
        # 本批读取自己的产物不拦截
        svc.assert_batch_access(scenario["batch_b"], scenario["b_file"])

    def test_provenance_reassignment_rejected(self, scenario):
        """同一产物禁止改挂到其他批次，防止同名文件串批。"""
        svc = scenario["svc"]
        rejected = svc.register_artifact_source(
            scenario["batch_a"], scenario["doc_b"], scenario["b_file"], "entity")
        assert "error" in rejected and "禁止改挂" in rejected["error"]

    def test_intake_list_evidence_for_legacy_doc_shape(self, scenario):
        """证据等级3：无批次字段、无登记但文档编号在接入清单中时可按清单定批。"""
        svc = scenario["svc"]
        doc_c = "DOCC-INTAKE-ONLY"
        batch_c = "BATCH-C-INTAKE"
        FileIntakeService().onboard_batch({
            "批次编号": batch_c, "来源系统": "来源系统C", "文件清单": f"{doc_c}.pdf",
            "文件类型": "PDF", "字节总量": "1024", "校验值": "", "采集方式": "接口",
            "接入游标": "0",
        })
        c_file = f"{doc_c}_parsed_entities.jsonl"
        svc._overwrite_jsonl(
            DATA_ROOT / "entities" / c_file,
            [_entity("ENT-C-001", doc_c, "C批机构", 0.9)],
        )
        result = svc.build_batch_evidence(batch_c)
        assert result["evidence_records"] == 1
        evidence = svc.load_batch_evidence(batch_c)
        assert evidence[0]["evidence_level"] == EVIDENCE_INTAKE_LIST

    def test_same_filename_across_batches_becomes_pending(self, scenario):
        """同名文件出现在两个批次清单且无更强证据时，不猜测，登记为待核。"""
        svc = scenario["svc"]
        shared_doc = "DOC-SHARED-NAME"
        FileIntakeService().onboard_batch({
            "批次编号": "BATCH-S1", "来源系统": "S1", "文件清单": f"{shared_doc}.pdf",
            "文件类型": "PDF", "字节总量": "1", "校验值": "", "采集方式": "接口",
            "接入游标": "0",
        })
        FileIntakeService().onboard_batch({
            "批次编号": "BATCH-S2", "来源系统": "S2", "文件清单": f"{shared_doc}.pdf",
            "文件类型": "PDF", "字节总量": "1", "校验值": "", "采集方式": "接口",
            "接入游标": "0",
        })
        shared_file = f"{shared_doc}_parsed_entities.jsonl"
        svc._overwrite_jsonl(
            DATA_ROOT / "entities" / shared_file,
            [_entity("ENT-SHARED-001", shared_doc, SAME_NAME_ORG, 0.8)],
        )
        svc.build_batch_evidence("BATCH-S1")
        pending = {(p["artifact_file"], p["record_id"]) for p in svc.list_unresolved()}
        assert (shared_file, "ENT-SHARED-001") in pending
        # 两个批次的证据集都不得收录这条记录
        assert svc.load_batch_evidence("BATCH-S1") == []
        assert svc.load_batch_evidence("BATCH-S2") == []

    def test_resolve_pending_assigns_legacy_to_confirmed_batch(self, scenario):
        """人工确认待核项后补登记来源，重算即进入确认批次而非继续悬空。"""
        svc = scenario["svc"]
        quality = ExtractQualityService()
        quality.check_from_batch(scenario["batch_b"], sample_ratio=1.0)

        resolved = svc.resolve_pending(
            scenario["legacy_file"], "ENT-LEG-001",
            scenario["batch_b"], operator="复核员甲")
        assert resolved.get("resolved") is True

        quality.check_from_batch(scenario["batch_b"], sample_ratio=1.0)
        evidence = svc.load_batch_evidence(scenario["batch_b"])
        record_ids = {e["record_id"] for e in evidence}
        assert "ENT-LEG-001" in record_ids
        # 待核清单中该记录已移除
        assert "ENT-LEG-001" not in {
            p["record_id"] for p in svc.list_unresolved() if p["artifact_file"] == scenario["legacy_file"]}

    def test_pending_reconciled_after_provenance_supplied(self, scenario):
        """后续关系/来源补齐后重算，历史待核项自动从待核清单核减。"""
        svc = scenario["svc"]
        # 初次构建：旧关系记录进入待核
        svc.build_batch_evidence(scenario["batch_a"])
        pending_before = {(p["artifact_file"], p["record_id"]) for p in svc.list_unresolved()}
        assert (scenario["legacy_rel_file"], "REL-LEG-001") in pending_before

        # 补齐处理来源（模拟后续关系文件提供可核对来源）
        svc.register_artifact_source(
            scenario["batch_a"], scenario["doc_legacy"],
            scenario["legacy_rel_file"], "relation")
        svc.build_batch_evidence(scenario["batch_a"])

        pending_after = {(p["artifact_file"], p["record_id"]) for p in svc.list_unresolved()}
        assert (scenario["legacy_rel_file"], "REL-LEG-001") not in pending_after
        # 补齐来源的关系进入 A 批证据集
        evidence_ids = {e["record_id"] for e in svc.load_batch_evidence(scenario["batch_a"])}
        assert "REL-LEG-001" in evidence_ids
