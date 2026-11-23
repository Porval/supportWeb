import {todayBeijing} from './model.js';
import {snapshotCell,beijing} from './dto.js';
export function snapshotWorkbook(XLSX,envelope){
 const s=envelope.data,meta=envelope.meta;
 const confirmedOnly=s.kind==='product_activity'&&s.exportScope==='confirmed_linked';
 if(!confirmedOnly&&(s.complete!==true||meta.complete!==true))throw new Error('结果不完整，不能作为全量导出');
 if(!Array.isArray(s.columns)||!Array.isArray(s.rows)||s.rows.length!==s.rowCount||s.rows.length===0)throw new Error('快照列/行/总数不一致或为空');
 if(s.rows.some(r=>!Array.isArray(r)||r.length!==s.columns.length))throw new Error('快照列序与行结构不匹配');
 const wb=XLSX.utils.book_new();const rows=s.rows.map(row=>row.map((v,i)=>snapshotCell(s.columns[i].key,v)));
 const sheet=XLSX.utils.aoa_to_sheet([s.columns.map(c=>c.key==='activityCategory'?'活动方式':c.label),...rows].map(row=>row.map(v=>({t:'s',v:String(v??'')}))));sheet['!autofilter']={ref:sheet['!ref']};sheet['!cols']=s.columns.map(()=>({wch:24}));XLSX.utils.book_append_sheet(wb,sheet,'商品活动明细');
 XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet([
 ['数据性质','活动工作台授权查询快照'],['mode',meta.mode],['来源',meta.dataOrigin??'待核实'],['datasetRevision',s.datasetRevision??'待核实'],['snapshotId',s.snapshotId],['筛选范围',JSON.stringify(s.filters)],['生成时间（北京时间）',beijing(s.generatedAt)??'待核实'],['数据获取时间（北京时间）',beijing(s.fetchedAt)??'待核实'],['结果行数',String(s.rowCount)],['导出范围',confirmedOnly?'当前筛选下所有页的已确认商品活动关联':'当前活动的完整商品清单'],['完整性',s.complete===true&&meta.complete===true?'当前范围关联完整':'仅含已确认关联，尚待确认或未提供明细的商品未列入'],['缺失说明','未知不填0；报名未知不等于未报名；标识/原文按文本保存'],['告警',(meta.warnings??[]).join('；')]
 ]),'导出说明');return {wb,rows};
}
export async function makeDownload(adapter,filter,activityId,signal){
 const snapshot=await adapter.snapshot(structuredClone(filter),activityId,signal);if(signal.aborted)throw new DOMException('Cancelled','AbortError');
 if(snapshot.meta.mode!==adapter.kind)throw new Error('快照与当前入口模式不符');
 const {wb,rows}=snapshotWorkbook(globalThis.XLSX,snapshot);const bytes=globalThis.XLSX.write(wb,{bookType:'xlsx',type:'array'});
 return {blob:new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),filename:'价探'+'商品活动_'+todayBeijing()+'.xlsx',rowCount:rows.length,snapshotId:snapshot.data.snapshotId};
}
