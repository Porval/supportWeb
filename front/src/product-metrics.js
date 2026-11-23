// Presentation of source-backed values. No inferred prices or margin arithmetic.
export const SORT_LABELS={activityPrice:'活动价',margin:'试算毛利',sales28d:'近4周销量',brand:'商品品牌'};
const DECIMAL=/^-?\d+(?:\.\d+)?$/;
const value=v=>(typeof v==='number'&&Number.isFinite(v)||typeof v==='string')&&DECIMAL.test(String(v))?String(v):null;
export function candidateBrand(row){const brands=[...new Set((row.candidates??[]).map(c=>c.brand?.trim()).filter(Boolean))];return brands.length===1?brands[0]:null}
export function selectedPlatformSales(row,platform){
 const data=row.operatingMetrics?.sales28d;
 return !platform||!data?.platformCode||data.platformCode===platform?data:null;
}
export function sortValue(row,field,platform){
 if(field==='brand')return candidateBrand(row);
 if(field==='activityPrice')return row.activityPriceCandidate?.status==='needs_review'?value(row.activityPriceCandidate.amountDecimal):null;
 if(field==='margin')return row.operatingMetrics?.trialMargin?.status==='ready'?value(row.operatingMetrics.trialMargin.amountDecimal):null;
 if(field==='sales28d'){
  const sales=selectedPlatformSales(row,platform);
  return sales?.status==='ready'?value(sales.quantity):null;
 }
 return null;
}
function compareDecimal(a,b){
 const negativeA=a.startsWith('-'),negativeB=b.startsWith('-');
 const [ai,af='']=a.replace(/^-/,'').split('.'),[bi,bf='']=b.replace(/^-/,'').split('.');
 const digits=Math.max(af.length,bf.length);
 const av=BigInt(ai+af.padEnd(digits,'0'))*(negativeA?-1n:1n),bv=BigInt(bi+bf.padEnd(digits,'0'))*(negativeB?-1n:1n);
 return av<bv?-1:av>bv?1:0;
}
const brandOrder=new Intl.Collator('zh-CN-u-co-pinyin',{sensitivity:'base'});
export function sortCandidateRows(rows,sort,platform){
 if(!SORT_LABELS[sort?.field])return rows;
 return rows.map((row,index)=>({row,index,key:sortValue(row,sort.field,platform)})).sort((a,b)=>{
  if(a.key===null)return b.key===null?a.index-b.index:1;
  if(b.key===null)return -1;
  const compared=sort.field==='brand'?brandOrder.compare(a.key,b.key):compareDecimal(a.key,b.key);
  return (sort.direction==='desc'?-compared:compared)||a.index-b.index;
 }).map(item=>item.row);
}

export async function querySales(adapter,platform,window,codes,signal,scope={}){
 const q=new URLSearchParams({platformCode:platform,window,codes:JSON.stringify(codes),...scope});
 const {data}=await adapter.request('/products/sales-history?'+q,{signal});
 if(data?.schemaVersion!=='activity-sales-history/1.0'||data.platformCode!==platform||data.window!==window||
    !Array.isArray(data.periods)||!Array.isArray(data.rows)||
    data.rows.some(row=>!codes.includes(row?.internalCode)||!Array.isArray(row.periods)))throw new Error('销量响应范围不一致');
 return data;
}
function salesQuantity(period){
 const quantity=value(period?.salesQty);
 return period?.status==='ready'&&quantity!==null?quantity+' 件':null;
}
export function salesComparison(row){
 const previous=salesQuantity(row?.periods.find(period=>period.key==='previous'));
 const percentage=value(row?.comparison?.changePct);
 if(row?.comparison?.status==='ready'&&percentage!==null&&previous){
  const sign=percentage.startsWith('-')||Number(percentage)===0?'':'+';
  return `环比 ${sign}${percentage}% · 上期 ${previous}`;
 }
 if(row?.comparison?.status==='zero_base'&&previous)return `上期 ${previous} · 环比不适用`;
 return previous?`环比待补 · 上期 ${previous}`:'环比待补';
}
export function salesStatus(result,period){
 if(result?.warnings?.some(warning=>warning.code==='SOURCE_NOT_CONFIGURED'))return '数据 Agent 未接入';
 if(!result||result.status==='source_unavailable')return 'Agent 数据暂不可用';
 if(period?.status==='missing_data')return '数据不完整';
 return '暂无数据';
}
export function createProductSales({root,adapter}){
 let activeSignal,cache=new Map();
 function mount(signal){
  if(activeSignal!==signal){activeSignal=signal;cache=new Map()}
  if(!signal||signal.aborted)return;
  const cells=[...root.querySelectorAll('[data-sales-recent]')];
  const byPlatform=new Map();
  const key=(platform,code)=>JSON.stringify([platform,code]);
  for(const cell of cells){
   const {salesPlatform:platform,salesRecent:code}=cell.dataset;
   if(!byPlatform.has(platform))byPlatform.set(platform,new Set());
   if(!cache.has(key(platform,code)))byPlatform.get(platform).add(code);
  }
  for(const [platform,missing] of byPlatform){
   const codes=[...missing];
   let queued=Promise.resolve();
   for(let offset=0;offset<codes.length;offset+=25){
    const batch=codes.slice(offset,offset+25);
    const response=queued.then(()=>signal.aborted?null:querySales(adapter,platform,'trailing_28_days_compare',batch,signal))
     .catch(()=>null);
    queued=response;
    for(const code of batch)cache.set(key(platform,code),response);
   }
  }
  for(const cell of cells){
   const {salesPlatform:platform,salesRecent:code}=cell.dataset;
   cache.get(key(platform,code)).then(result=>{
    if(signal.aborted||activeSignal!==signal||!cell.isConnected)return;
    const row=result?.rows.find(item=>item.internalCode===code);
    const current=row?.periods.find(period=>period.key==='current');
    const quantity=salesQuantity(current);
    const recorded=value(current?.recordedSalesQty);
    cell.textContent=quantity??(current?.status==='missing_data'&&recorded!==null
     ?`已记录 ${recorded} 件`:salesStatus(result,current));
    if(result&&result.status!=='source_unavailable'){
     const note=document.createElement('small');
     note.textContent=salesComparison(row);
     cell.append(note);
    }
    const dates=result?.periods.map(period=>`${period.key==='current'?'本期':'上期'} ${period.startDate} 至 ${period.endDate}`);
    if(dates?.length)cell.title=dates.join('；');
   });
  }
 }
 async function history(form,signal){
  const output=form.parentElement.querySelector('[data-sales-history-result]');
  const submit=form.querySelector('button[type=submit]');
  if(!output||submit.disabled||signal?.aborted)return;
  const [code,platform]=JSON.parse(form.elements.product.value);
  submit.disabled=true;
  output.textContent='正在查询销量…';
  try{
   const result=await querySales(adapter,platform,'last_3_complete_months',[code],signal);
   if(signal?.aborted||!output.isConnected)return;
   const row=result.rows.find(item=>item.internalCode===code);
   if(!result.periods.length){
    output.textContent=salesStatus(result);
    return;
   }
   const list=document.createElement('dl');
   list.className='info-grid';
   for(const period of result.periods){
    const month=row?.periods?.find(item=>item.key===period.key);
    const group=document.createElement('div'), title=document.createElement('dt'), amount=document.createElement('dd');
    title.textContent=period.startDate+' 至 '+period.endDate;
    amount.textContent=salesQuantity(month)??salesStatus(result,month);
    group.append(title,amount);
    list.append(group);
   }
   output.replaceChildren(list);
  }catch{
   if(!signal?.aborted&&output.isConnected)output.textContent='Agent 数据暂不可用，请稍后再试。';
  }finally{if(form.isConnected)submit.disabled=false}
 }
 return {mount,history};
}
