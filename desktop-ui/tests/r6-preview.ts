import { createApp } from 'vue'
import App from '../src/App.vue'
import { createMockApi } from '../src/api/mock'
import { CloudError } from '../src/api/cloud'
import '../src/style.css'
createApp(App,{testTransport:'R6 模型授权合成检查，不使用真实密钥或请求供应商',createApi:()=>({...createMockApi(),mode:'mock' as const,model:{
 async status(){return{configured:true as const,provider:'合成测试供应商',model:'fixture-model',region:'合成测试区域',requiresExplicitConsent:true as const,executionAuthorized:false as const}},
 async ask(query:string){if(query.includes('不确定'))throw new CloudError('MODEL_OUTCOME_UNCERTAIN','合成失败',502,false);return{text:'R6 合成模型回答（没有调用真实供应商）。',matches:[],historySync:'not_synced' as const,provider:'合成测试供应商',model:'fixture-model'}}
}})}).mount('#app')
