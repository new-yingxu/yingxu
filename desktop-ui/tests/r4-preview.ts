import { createApp } from 'vue'
import App from '../src/App.vue'
import { createMockApi } from '../src/api/mock'
import { parseCloudReply } from '../src/api/cloud'
import '../src/style.css'
createApp(App, { testTransport: 'R4 合成响应界面检查；非真实身份、非线上服务', createApi: () => {
  const mock=createMockApi()
  mock.agent.ask=async()=>parseCloudReply({answer:'R4 合成响应：找到一条任务摘要（非线上验证）。',persisted:false,matches:[{kind:'job',deviceId:'fixture-device',jobId:'fixture-job',name:'隔离测试任务',state:'success',sequence:3,occurredAt:'2026-10-26T00:00:00Z',source:'synced_job_snapshot'}]})
  return {...mock,mode:'mock' as const}
}}).mount('#app')
