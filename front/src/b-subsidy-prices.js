import {h, input, rows, table} from './b-common.js';

const fields=[
 ['systemPriceDecimal','系统活动价'],
 ['subsidyDecimal','申请补贴'],
 ['netPriceDecimal','补贴后单价'],
];
const name=(id,key)=>'subsidy:'+id+':'+key;
const needsCorrection=row=>['missing','conflict'].includes(row.status);

export function subsidyPricesHtml(items=[],{editable=false,values={}}={}){
 if(!items.length)return '';
 const issueCount=items.filter(needsCorrection).length;
 const status={ready:'已读取',corrected:'已补正',missing:'价格待补',conflict:'价格冲突'};
 const rendered=items.map(item=>rows([
  h(item.name??'商品名称待核对')+'<small>'+h(item.source.sheet)+' · 第 '+h(item.source.row)+' 行</small>',
  ...fields.map(([key,label])=>{
   const old=item.source.cells?.[key]?.raw;
   return h(item[key]??'待补')+
    (old?.startsWith('=')?'<small>按原表公式计算</small>':
     item.status==='corrected'&&old!==item[key]?'<small>原表 '+h(old||'未填')+'</small>':'')+
    (editable&&needsCorrection(item)?input(name(item.rowId,key),label,values[name(item.rowId,key)]??item[key],{maxLength:15}):'');
  }),
  h(status[item.status]??'待核对')+
   (editable&&needsCorrection(item)?input(name(item.rowId,'reason'),'修正说明',values[name(item.rowId,'reason')],{maxLength:200}):
    item.correction?.reason?'<small>'+h(item.correction.reason)+'</small>':''),
 ]));
 return '<details class="b-note"'+(issueCount?' open':'')+'><summary>单品补贴价格（'+items.length+' 条'+
  (issueCount?'，'+issueCount+' 条待核对':'')+'）</summary>'+
  (editable&&issueCount?'<p class="hint">仅填写要补正的行，并写明修正说明。待核对的价格可以先保存，修正后才可通过终审。</p>':'')+
  table(['原表商品',...fields.map(([,label])=>label),'价格核对'],rendered)+'</details>';
}

export function subsidyCorrectionValues(items=[],form){
 return Object.fromEntries(items.filter(needsCorrection).flatMap(item=>
  [...fields.map(([key])=>key),'reason'].map(key=>[name(item.rowId,key),form.get(name(item.rowId,key))])));
}

export function subsidyCorrections(items=[],values={}){
 const result=[];
 for(const item of items.filter(needsCorrection)){
  const reason=values[name(item.rowId,'reason')]?.trim();
  const prices=Object.fromEntries(fields.map(([key])=>[key,(values[name(item.rowId,key)]??item[key]??'').trim()]));
  const changed=fields.some(([key])=>prices[key]!==String(item[key]??''));
  if(!changed&&!reason)continue;
  if(!reason||!Object.values(prices).every(value=>/^\d{1,12}(?:\.\d{1,2})?$/.test(value)))
   throw new Error('请填齐待补正行的三项价格和修正说明。');
  result.push({rowId:item.rowId,...prices,reason});
 }
 return result;
}
