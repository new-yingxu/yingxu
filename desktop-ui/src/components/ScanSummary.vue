<script setup lang="ts">
import { computed } from 'vue'
import type { ScanReport, IndexResult } from '../api/contracts'
const props=defineProps<{report: ScanReport | IndexResult | null}>()
const total=computed(()=>props.report&&('skippedTotal' in props.report ? props.report.skippedTotal : props.report.skippedCount??0))
const reason=(code:string)=>({unportable_name:'文件名不兼容',symlink:'符号链接',unreadable:'无法读取'}[code]??code)
</script>
<template><section v-if="report&&total" class="scan-skips panel" aria-label="扫描跳过报告"><strong>已跳过 {{ total }} 项，其余文件继续处理</strong><details><summary>查看跳过原因</summary><ul><li v-for="(item,index) in (report.skipped??[]).slice(0,200)" :key="index"><b>{{ item.relative }}</b> · {{ reason(item.reason) }}<small v-if="item.detail"> · {{ item.detail }}</small></li></ul><p v-if="total > (report.skipped?.length??0) || (report.skipped?.length??0)>200">当前仅展示部分记录；合计 {{ total }} 项。</p></details><p>扫描只读，跳过不代表文件已删除。</p></section></template>
<style scoped>.scan-skips{margin-bottom:16px;font-size:13px}.scan-skips summary{cursor:pointer;margin-top:8px}.scan-skips ul{max-height:240px;overflow:auto;padding-left:22px}.scan-skips li{padding:5px 0;overflow-wrap:anywhere}.scan-skips p{color:var(--muted);margin-bottom:0}</style>
