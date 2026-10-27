/** Independent native IPC contract for local Agent file actions.
 * This module intentionally stays separate from the existing read/open Agent API.
 */
export type LocalFileActionTool = 'rename_item' | 'move_item' | 'cleanup_quarantine'

export interface LocalFileActionPreview {
  actionId: string
  tool: LocalFileActionTool
  mediaId?: string
  objectName: string
  sourcePath?: string
  targetName?: string
  targetPath: string
  sha256?: string
  effect: string
  requiresConfirmation: true
  expiresAtMs?: number
  expiresAt?: string
}

export interface LocalFileActionReceipt {
  actionId: string
  tool: LocalFileActionTool
  status: 'succeeded' | 'partial' | 'completed_unindexed' | 'failed' | 'declined' | 'expired' | 'cancelled'
  mediaId?: string
  sourcePath?: string
  targetPath?: string
  sha256?: string
  sourcePreserved?: boolean
  destinationCreated?: boolean
  userConfirmed: boolean
  cloudExecutionAuthorized: false
  error?: { code: string; message: string } | null
  [key: string]: unknown
}

export interface LocalDirectoryGrant {
  path: string
  purpose: 'source' | 'destination'
  grantId: string
}

export type NativeInvoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label}结构无效`)
  return value as Record<string, unknown>
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label}无效`)
  return value
}

/** Keep the opaque destination grant returned by the native folder picker. */
export async function selectLocalDirectory(
  invoke: NativeInvoke,
  purpose: LocalDirectoryGrant['purpose'],
): Promise<LocalDirectoryGrant | null> {
  const raw = await invoke<unknown>('select_directory', { purpose })
  if (raw == null) return null
  const row = record(raw, '目录授权')
  if (row.purpose !== purpose) throw new Error('目录授权用途不匹配')
  return {
    path: nonEmptyString(row.path, '目录路径'),
    purpose,
    grantId: nonEmptyString(row.grantId, '目录授权编号'),
  }
}

export async function prepareLocalFileAction(
  invoke: NativeInvoke,
  request:
    | { tool: 'rename_item'; mediaId: string; newName: string }
    | { tool: 'move_item'; mediaId: string; destinationGrantId: string }
    | { tool: 'cleanup_quarantine'; quarantineId: string },
): Promise<LocalFileActionPreview> {
  const raw = await invoke<unknown>('agent_prepare_file_action', {
    tool: request.tool,
    mediaId: 'mediaId' in request ? request.mediaId : undefined,
    newName: request.tool === 'rename_item' ? request.newName : undefined,
    destinationGrantId: request.tool === 'move_item' ? request.destinationGrantId : undefined,
    quarantineId: request.tool === 'cleanup_quarantine' ? request.quarantineId : undefined,
  })
  const row = record(raw, '本地操作预览')
  if (row.tool !== request.tool || row.requiresConfirmation !== true) throw new Error('本地操作预览缺少用户确认门槛')
  return {
    actionId: nonEmptyString(row.actionId, '操作编号'),
    tool: row.tool as LocalFileActionTool,
    ...(typeof row.mediaId === 'string' ? { mediaId: row.mediaId } : {}),
    objectName: nonEmptyString(row.objectName, '素材名称'),
    ...(typeof row.sourcePath === 'string' ? { sourcePath: row.sourcePath } : {}),
    ...(typeof row.targetName === 'string' ? { targetName: row.targetName } : {}),
    targetPath: nonEmptyString(row.targetPath, '目标位置'),
    ...(typeof row.sha256 === 'string' ? { sha256: row.sha256 } : {}),
    effect: nonEmptyString(row.effect, '操作影响说明'),
    requiresConfirmation: true,
    ...(typeof row.expiresAtMs === 'number' ? { expiresAtMs: row.expiresAtMs } : {}),
    ...(typeof row.expiresAt === 'string' ? { expiresAt: row.expiresAt } : {}),
  }
}

/** Declining is a first-class receipt; accepted operations still require this explicit call. */
export async function confirmLocalFileAction(
  invoke: NativeInvoke,
  actionId: string,
  accepted: boolean,
): Promise<LocalFileActionReceipt> {
  const raw = await invoke<unknown>('agent_confirm_file_action', { actionId, accepted })
  const row = record(raw, '本地操作回执')
  if (row.actionId !== actionId || row.userConfirmed !== accepted || row.cloudExecutionAuthorized !== false) {
    throw new Error('本地操作回执与请求不匹配')
  }
  if (!['succeeded', 'partial', 'completed_unindexed', 'failed', 'declined', 'expired', 'cancelled'].includes(String(row.status))) {
    throw new Error('本地操作回执状态无效')
  }
  return row as unknown as LocalFileActionReceipt
}
