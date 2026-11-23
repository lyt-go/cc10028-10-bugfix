<template>
  <section class="page" data-module="batch_quality">
    <header class="page-head">
      <div>
        <h2>批次质检证据集</h2>
        <p class="page-desc">
          实体与关系源记录先做批次归属校验再质检：同名机构不会跨批混入，
          无法判定来源的历史产物登记为待核，重算报告不会把历史数据算进当前批。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="refresh-pending" type="button" @click="reloadPending">
          刷新待核清单
        </button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="runCheck">
      <label class="filter-item">
        <span>批次编号</span>
        <input v-model="batchKey" data-testid="batch-key" placeholder="如 BATCH-A" required />
      </label>
      <label class="filter-item">
        <span>抽样比例</span>
        <input v-model.number="sampleRatio" data-testid="sample-ratio" type="number"
               min="0.01" max="1" step="0.01" required />
      </label>
      <button class="btn primary" data-testid="run-quality-check" type="submit"
              :disabled="busy">
        {{ busy ? '重算中…' : '重算质检报告' }}
      </button>
      <button class="btn" data-testid="dispatch-reviews" type="button" :disabled="busy"
              @click="dispatch">
        提交复核派单
      </button>
    </form>

    <p v-if="message" class="error-text" :class="{ ok: !hasError }">{{ message }}</p>

    <div v-if="report" class="stat-row">
      <article class="stat-card">
        <span class="stat-label">报告编号</span>
        <strong>{{ report['报告编号'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">抽检条目</span>
        <strong>{{ report['抽检条目'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">错抽 / 漏抽</span>
        <strong>{{ report['错抽数量'] }} / {{ report['漏抽数量'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">准确率</span>
        <strong>{{ report['准确率'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核项</span>
        <strong>{{ report['待核项数量'] }}</strong>
      </article>
    </div>

    <p v-if="report" class="status-legend">
      <span class="legend-item">质检结论：{{ report['质检结论'] }}</span>
      <span class="legend-item">证据集：{{ report['证据集文件'] }}</span>
    </p>

    <h3>批次质检证据（{{ evidence.length }} 条，仅含已归属到本批的记录）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>类型</th>
          <th>记录编号</th>
          <th>实体/关系</th>
          <th>文档编号</th>
          <th>置信度</th>
          <th>产物文件</th>
          <th>归属证据</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in evidence" :key="`${item.artifact_file}-${item.record_id}`"
            :data-testid="`evidence-row-${item.record_id}`">
          <td>{{ item.artifact_type === 'entity' ? '实体' : '关系' }}</td>
          <td>{{ item.record_id }}</td>
          <td>{{ item.target_text }}</td>
          <td>{{ item.doc_id }}</td>
          <td>{{ item.confidence }}</td>
          <td>{{ item.artifact_file }}</td>
          <td>证据等级{{ item.evidence_level }}：{{ item.evidence_reason }}</td>
        </tr>
        <tr v-if="!evidence.length">
          <td colspan="7" class="empty-state">暂无证据，请先输入批次编号重算质检报告</td>
        </tr>
      </tbody>
    </table>

    <h3>复核单（按来源负责组分流，跨组领取会被拒绝）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>复核编号</th>
          <th>批次</th>
          <th>复核对象</th>
          <th>记录编号</th>
          <th>来源负责组</th>
          <th>证据归属</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in reviewTasks" :key="task.复核编号"
            :data-testid="`review-row-${task.复核编号}`">
          <td>{{ task.复核编号 }}</td>
          <td>{{ task.批次编号 }}</td>
          <td>{{ task.复核对象 }}</td>
          <td>{{ task.记录编号 }}</td>
          <td>{{ task.来源负责组 }}</td>
          <td>{{ task.证据归属 }}</td>
          <td>{{ task.status }}</td>
          <td class="row-actions">
            <button class="link" type="button"
                    @click="openClaim(task)">领取/结案</button>
          </td>
        </tr>
        <tr v-if="!reviewTasks.length">
          <td colspan="8" class="empty-state">暂无复核单，派单后在此展示</td>
        </tr>
      </tbody>
    </table>

    <h3>待核历史产物（{{ pending.length }} 条，不随任何批次重算入账）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>产物文件</th>
          <th>类型</th>
          <th>记录编号</th>
          <th>文档编号</th>
          <th>实体/关系</th>
          <th>待核原因</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in pending" :key="`${item.artifact_file}-${item.record_id}`"
            :data-testid="`pending-row-${item.record_id}`">
          <td>{{ item.artifact_file }}</td>
          <td>{{ item.artifact_type === 'entity' ? '实体' : '关系' }}</td>
          <td>{{ item.record_id }}</td>
          <td>{{ item.doc_id }}</td>
          <td>{{ item.target_text }}</td>
          <td>{{ item.reason }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openResolve(item)">确认归属</button>
          </td>
        </tr>
        <tr v-if="!pending.length">
          <td colspan="7" class="empty-state">没有待核历史产物</td>
        </tr>
      </tbody>
    </table>

    <div v-if="claimTask" class="modal-backdrop" data-testid="claim-modal">
      <form class="modal" @submit.prevent="submitClaim">
        <h3>领取并结案：{{ claimTask.复核编号 }}</h3>
        <p>来源负责组：{{ claimTask.来源负责组 }}（只有该组可领取）</p>
        <label class="create-field">
          <span>领取组</span>
          <input v-model="claimGroup" data-testid="claim-group" required />
        </label>
        <label class="create-field">
          <span>复核人</span>
          <input v-model="claimReviewer" data-testid="claim-reviewer" required />
        </label>
        <label class="create-field">
          <span>复核结论</span>
          <input v-model="claimConclusion" data-testid="claim-conclusion" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="claimTask = null">取消</button>
          <button class="btn primary" type="submit">提交结论</button>
        </div>
      </form>
    </div>

    <div v-if="resolveItem" class="modal-backdrop" data-testid="resolve-modal">
      <form class="modal" @submit.prevent="submitResolve">
        <h3>确认待核记录归属</h3>
        <p>{{ resolveItem.artifact_file }} # {{ resolveItem.record_id }}</p>
        <label class="create-field">
          <span>确认批次编号</span>
          <input v-model="resolveBatch" data-testid="resolve-batch" required />
        </label>
        <label class="create-field">
          <span>操作人</span>
          <input v-model="resolveOperator" data-testid="resolve-operator" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="resolveItem = null">取消</button>
          <button class="btn primary" type="submit">确认并补登记来源</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  claimReview,
  dispatchReviews,
  loadBatchReviewTasks,
  loadQualityEvidence,
  loadUnresolvedPending,
  resolvePending,
  runQualityCheck,
  submitReviewConclusion,
} from '@/api/backend-service'
import type {
  BatchEvidenceItem,
  EntryRow,
  ReviewTask,
  UnresolvedItem,
} from '@/data/types'

const batchKey = ref('')
const sampleRatio = ref(1)
const busy = ref(false)
const message = ref('')
const hasError = ref(false)
const report = ref<EntryRow | null>(null)
const evidence = ref<BatchEvidenceItem[]>([])
const reviewTasks = ref<ReviewTask[]>([])
const pending = ref<UnresolvedItem[]>([])

const claimTask = ref<ReviewTask | null>(null)
const claimGroup = ref('')
const claimReviewer = ref('')
const claimConclusion = ref('')
const resolveItem = ref<UnresolvedItem | null>(null)
const resolveBatch = ref('')
const resolveOperator = ref('')

function notify(text: string, isError = false) {
  message.value = text
  hasError.value = isError
}

async function runCheck() {
  if (!batchKey.value.trim()) return
  busy.value = true
  try {
    const result = (await runQualityCheck(batchKey.value.trim(), sampleRatio.value)) as {
      report: EntryRow
    }
    report.value = result.report
    evidence.value = await loadQualityEvidence(batchKey.value.trim())
    reviewTasks.value = await loadBatchReviewTasks(batchKey.value.trim())
    await reloadPending()
    notify(`质检报告已重算：${result.report['质检结论']}`)
  } catch (error) {
    notify(error instanceof Error ? error.message : '质检重算失败', true)
  } finally {
    busy.value = false
  }
}

async function dispatch() {
  if (!batchKey.value.trim()) return
  busy.value = true
  try {
    const result = await dispatchReviews(batchKey.value.trim())
    reviewTasks.value = await loadBatchReviewTasks(batchKey.value.trim())
    notify(`派单成功：${result.tasks_created} 张低置信度复核单 → ${result.groups.join('、')}`)
  } catch (error) {
    notify(error instanceof Error ? error.message : '复核派单失败', true)
  } finally {
    busy.value = false
  }
}

async function reloadPending() {
  try {
    pending.value = await loadUnresolvedPending()
  } catch (error) {
    notify(error instanceof Error ? error.message : '待核清单读取失败', true)
  }
}

function openClaim(task: ReviewTask) {
  claimTask.value = task
  claimGroup.value = task.来源负责组
  claimReviewer.value = ''
  claimConclusion.value = ''
}

async function submitClaim() {
  if (!claimTask.value) return
  try {
    await claimReview(claimTask.value.复核编号, claimGroup.value)
    await submitReviewConclusion(
      claimTask.value.复核编号,
      claimConclusion.value,
      claimReviewer.value,
      claimGroup.value,
    )
    claimTask.value = null
    reviewTasks.value = await loadBatchReviewTasks(batchKey.value.trim())
    notify('复核单已由对应来源负责组领取并结案')
  } catch (error) {
    notify(error instanceof Error ? error.message : '领取或结案失败', true)
  }
}

function openResolve(item: UnresolvedItem) {
  resolveItem.value = item
  resolveBatch.value = batchKey.value.trim()
  resolveOperator.value = ''
}

async function submitResolve() {
  if (!resolveItem.value) return
  try {
    await resolvePending(
      resolveItem.value.artifact_file,
      resolveItem.value.record_id,
      resolveBatch.value.trim(),
      resolveOperator.value,
    )
    resolveItem.value = null
    await reloadPending()
    notify('待核记录已补登记处理来源，重算后进入确认批次')
  } catch (error) {
    notify(error instanceof Error ? error.message : '待核确认失败', true)
  }
}

onMounted(reloadPending)
</script>
