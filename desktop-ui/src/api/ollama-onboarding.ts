import type { ModelStatus } from './contracts'

export type OllamaSetupState = 'ready' | 'configured' | 'ollama_unavailable' | 'model_not_installed' | 'sidecar_unavailable' | 'unknown'

export interface OllamaSetupView {
  state: OllamaSetupState
  title: string
  detail: string
}

export const REQUIRED_LOCAL_MODEL = 'qwen2.5:7b'
export const REQUIRED_MODEL_PULL_COMMAND = `ollama pull ${REQUIRED_LOCAL_MODEL}`

export function ollamaSetupView(status: ModelStatus | null, connected = true): OllamaSetupView {
  if (!connected || !status) return {
    state: 'unknown',
    title: '状态待确认',
    detail: '暂时无法读取本机 Agent 状态。刷新后再试；不会切换到云端模型。',
  }

  switch (status.availability) {
    case 'configured':
      return { state: 'configured', title: '云端模型已配置', detail: `本机 Agent 使用 ${status.configured ? status.provider : '所选供应商'} 云端模型；请求逐次确认。需要完全离线时，可另行安装并启动 Ollama。` }
    case 'ready':
      return {
        state: 'ready',
        title: '本机模型已就绪',
        detail: `${status.configured ? status.model : REQUIRED_LOCAL_MODEL} 已可供影序本机 Agent 使用。每次运行仍需你单独确认。`,
      }
    case 'ollama_unavailable':
      return {
        state: 'ollama_unavailable',
        title: '尚未检测到 Ollama',
        detail: '请先手动安装并启动 Ollama，再刷新状态。影序不会自动安装软件或切换到云端。',
      }
    case 'model_not_installed':
      return {
        state: 'model_not_installed',
        title: 'Ollama 已运行，所需模型尚未安装',
        detail: `当前桌面 Agent 需要本机模型 ${REQUIRED_LOCAL_MODEL}。请在终端手动运行下载命令，再刷新状态。`,
      }
    case 'sidecar_unavailable':
      return {
        state: 'sidecar_unavailable',
        title: '影序本机 Agent 服务不可用',
        detail: '请重启影序后刷新状态。安装 Ollama 或下载模型不能修复此服务状态；不会回退到云端。',
      }
    default:
      return {
        state: 'unknown',
        title: '状态待确认',
        detail: '本机 Agent 状态不完整。刷新状态或重启影序后再试；不会切换到云端模型。',
      }
  }
}
