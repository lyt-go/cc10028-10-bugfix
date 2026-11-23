import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  BatchEvidenceItem,
  EntryRow,
  OverviewResult,
  PageResult,
  ReviewTask,
  UnresolvedItem,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ===== 批次归属与质检证据集专用接口 =====

async function postJson(path: string, body: Record<string, unknown> = {}): Promise<unknown> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail ?? `接口返回 ${response.status}`)
  }
  return response.json()
}

/** 重算批次质检：归属校验 → 生成证据集 → 出具报告（幂等覆盖）。 */
export function runQualityCheck(batchKey: string, sampleRatio = 0.1): Promise<unknown> {
  return postJson(`/api/batches/${encodeURIComponent(batchKey)}/quality-check`, {
    sample_ratio: sampleRatio,
  })
}

/** 查看批次质检证据集，每条证据带归属依据，详情不再出现跨批同名记录。 */
export async function loadQualityEvidence(batchKey: string): Promise<BatchEvidenceItem[]> {
  const response = await request(
    `/api/batches/${encodeURIComponent(batchKey)}/quality-evidence`,
  )
  if (!response.ok) throw new Error('批次质检证据集读取失败')
  const payload = (await response.json()) as { items: BatchEvidenceItem[] }
  return payload.items
}

/** 待核清单：现有字段无法判定来源的历史产物，重算时不计入任何当前批次。 */
export async function loadUnresolvedPending(): Promise<UnresolvedItem[]> {
  const response = await request('/api/batches/unresolved/pending')
  if (!response.ok) throw new Error('待核清单读取失败')
  const payload = (await response.json()) as { items: UnresolvedItem[] }
  return payload.items
}

/** 人工确认待核记录的真实批次，补齐处理来源登记。 */
export function resolvePending(
  artifactFile: string,
  recordId: string,
  batchKey: string,
  operator = '',
): Promise<unknown> {
  const query = new URLSearchParams({ artifact_file: artifactFile, record_id: recordId })
  return postJson(`/api/batches/unresolved/resolve?${query.toString()}`, {
    batch_key: batchKey,
    operator,
  })
}

/** 根据批次质检证据集提交复核派单请求，按来源负责组分流低置信度单据。 */
export function dispatchReviews(
  batchKey: string,
  threshold = 0.8,
): Promise<{ tasks_created: number; groups: string[] }> {
  return postJson(`/api/batches/${encodeURIComponent(batchKey)}/review-dispatch`, {
    threshold,
  }) as Promise<{ tasks_created: number; groups: string[] }>
}

/** 查看批次复核单，单据标明抽到了哪个批次的哪个实体或关系。 */
export async function loadBatchReviewTasks(batchKey: string): Promise<ReviewTask[]> {
  const response = await request(
    `/api/batches/${encodeURIComponent(batchKey)}/review-tasks`,
  )
  if (!response.ok) throw new Error('批次复核单读取失败')
  const payload = (await response.json()) as { items: ReviewTask[] }
  return payload.items
}

/** 对应来源负责组领取复核单，跨组/跨批领取会被后端归属权限拒绝。 */
export function claimReview(reviewId: string, group: string): Promise<unknown> {
  return postJson(`/api/batches/reviews/${encodeURIComponent(reviewId)}/claim`, { group })
}

/** 负责组登记复核结论并结案。 */
export function submitReviewConclusion(
  reviewId: string,
  conclusion: string,
  reviewer: string,
  group: string,
): Promise<unknown> {
  return postJson(`/api/batches/reviews/${encodeURIComponent(reviewId)}/conclusion`, {
    conclusion,
    reviewer,
    group,
  })
}
