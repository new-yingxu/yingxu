import { CloudError, type CloudClient, type CloudJobSnapshot } from './cloud'
import type { AgentReply } from './contracts'

export interface PendingTurn { sessionId: string; turnId: string; query: string }
export interface OwnerCache {
  ownerId: string
  // Server cursors are independent, never inferred from a device's sequence.
  cursors: { tasks: number; deletions: number; invalidations: number }
  tasks: Map<string, CloudJobSnapshot>
  turns: Map<string, { sessionId: string; reply: AgentReply }>
  deletedSessions: Set<string>
  pending: Map<string, PendingTurn>
  pathsRedacted: boolean
  ready: boolean
}
export function emptyOwnerCache(ownerId: string): OwnerCache {
  return { ownerId, cursors: { tasks: 0, deletions: 0, invalidations: 0 }, tasks: new Map(), turns: new Map(), deletedSessions: new Set(), pending: new Map(), pathsRedacted: false, ready: false }
}
/** A real implementation must provide atomic commit/rollback and serialize transactions per owner.
 * Do not implement this as independent localStorage writes. No production persistence is installed yet.
 */
export interface OwnerCacheStore {
  read(ownerId: string): Promise<OwnerCache>
  transaction(ownerId: string, change: (draft: OwnerCache) => void): Promise<void>
}
const turnKey = (turn: PendingTurn) => JSON.stringify([turn.sessionId, turn.turnId])
function forgetSession(state: OwnerCache, sessionId: string) {
  state.deletedSessions.add(sessionId)
  for (const [key, turn] of state.turns) if (turn.sessionId === sessionId) state.turns.delete(key)
  for (const [key, turn] of state.pending) if (turn.sessionId === sessionId) state.pending.delete(key)
}
function redactTasks(state: OwnerCache) {
  state.pathsRedacted = true
  for (const task of state.tasks.values()) { delete task.payload.source; delete task.payload.paths }
}
/** Pure orchestration for one verified owner. Create a fresh scope after logout/account switch.
 * bindClient must bind that owner's short-lived token AND this scope's AbortSignal.
 * Deliberately unused by the application factory until the native identity/persistence bridge exists.
 */
export function createOwnerSync(ownerId: string, store: OwnerCacheStore, bindClient: (signal: AbortSignal) => CloudClient) {
  if (!ownerId) throw new CloudError('AUTH_REQUIRED', '需要已验证的账号')
  const controller = new AbortController()
  const client = bindClient(controller.signal)
  let stopped = false
  let serial: Promise<unknown> = Promise.resolve()
  const guard = () => { if (stopped) throw new CloudError('OWNER_SESSION_STOPPED', '旧账号同步已停止') }
  async function read() { guard(); const state = await store.read(ownerId); guard(); if (state.ownerId !== ownerId) throw new CloudError('OWNER_MISMATCH', '账号缓存不匹配'); return state }
  async function commit(change: (draft: OwnerCache) => void) {
    guard()
    await store.transaction(ownerId, draft => {
      guard()
      if (draft.ownerId !== ownerId) throw new CloudError('OWNER_MISMATCH', '账号缓存不匹配')
      change(draft)
    })
    guard()
  }
  function exclusive<T>(work: () => Promise<T>): Promise<T> {
    const result = serial.then(() => { guard(); return work() })
    serial = result.catch(() => {}) // keep the queue usable; the original rejection is returned intact.
    return result
  }
  async function notifications() {
    let more = true
    while (more) {
      const after = (await read()).cursors.invalidations
      const page = await client.invalidations(after); guard()
      await commit(state => {
        // The same transaction cleans ALL cached tasks and advances the notification cursor.
        if (page.invalidations.length) redactTasks(state)
        state.cursors.invalidations = page.nextCursor
      })
      more = page.hasMore
    }
    more = true
    while (more) {
      const after = (await read()).cursors.deletions
      const page = await client.deletions(after); guard()
      await commit(state => {
        for (const deletion of page.deletions) forgetSession(state, deletion.sessionId)
        state.cursors.deletions = page.nextCursor
      })
      more = page.hasMore
    }
  }
  return {
    stop() { stopped = true; controller.abort() },
    sync() {
      return exclusive(async () => {
        await commit(state => { state.ready = false })
        // Do not expose a newly fetched snapshot until the post-write notification barrier passes.
        await notifications()
        let more = true
        while (more) {
          const after = (await read()).cursors.tasks
          const page = await client.snapshot(after); guard()
          await commit(state => {
            for (const item of page.events) {
              const key = JSON.stringify([item.deviceId, item.payload.jobId])
              const current = state.tasks.get(key)
              if (!current || item.sequence > current.sequence) {
                const task = structuredClone(item)
                if (state.pathsRedacted) { delete task.payload.source; delete task.payload.paths }
                state.tasks.set(key, task)
              }
            }
            state.cursors.tasks = page.nextCursor
          })
          // Required even on the last page: a preference/delete can change during the snapshot request.
          await notifications()
          more = page.hasMore
        }
        await commit(state => { state.ready = true })
      })
    },
    enqueue(turn: PendingTurn) {
      return exclusive(async () => {
        if (!turn.sessionId || !turn.turnId || !turn.query.trim()) throw new CloudError('INVALID_TURN', '会话、轮次和查询不能为空')
        await commit(state => {
          if (state.deletedSessions.has(turn.sessionId)) throw new CloudError('SESSION_DELETED', '该会话已删除，请新建会话')
          const key = turnKey(turn), current = state.pending.get(key)
          if (current && current.query !== turn.query) throw new CloudError('TURN_ID_CONFLICT', '同一轮次不能更改查询')
          state.pending.set(key, { ...turn })
        })
      })
    },
    flushOne() {
      return exclusive(async () => {
        await notifications()
        const state = await read(), turn = state.pending.values().next().value
        if (!turn) return null
        try {
          const reply = await client.ask(turn.query, { sessionId: turn.sessionId, turnId: turn.turnId }); guard()
          // Delete may have raced with the response. Read tombstones before committing any reply.
          await notifications()
          await commit(draft => {
            if (draft.deletedSessions.has(turn.sessionId)) throw new CloudError('SESSION_DELETED', '该会话已删除，回答不进入历史')
            draft.pending.delete(turnKey(turn))
            if (reply.historySync === 'synced') draft.turns.set(turnKey(turn), { sessionId: turn.sessionId, reply })
          })
          return reply // persisted:false remains a real answer, explicitly marked not_synced.
        } catch (error) {
          guard()
          if (error instanceof CloudError && error.code === 'SESSION_DELETED') {
            await commit(draft => { forgetSession(draft, turn.sessionId) })
          }
          // Other failures preserve original turnId/query for an explicit retry, never replace IDs.
          throw error
        }
      })
    },
    // A stopped scope has no readable view, even if its old DB transaction already committed.
    async view() {
      const state = await read()
      if (!state.ready) throw new CloudError('SYNC_NOT_READY', '通知检查未完成，暂不展示缓存')
      return structuredClone(state)
    },
  }
}
