import {esc,fmt} from '../utils/format.js';import {table} from './common/table.js';
const STAGE_NAMES=['聚合开单日期','搜索 1 个切点','搜索 2 个切点','搜索 3 个切点'];
const pct=v=>v==null?'—':(v*100).toFixed(2)+'%';
export function segment(run,seg,canStart){if(!run)return '';const state=seg?.status||'idle';
 const head='<div class="segment-head"><h3>快慢段复盘</h3><p class="segment-note">按报告时区的开单日期，为所有小组选同一套切点；段内用各组超时率预测逐单是否超时，取总平方误差最小者。</p></div>';
 if(run.status!=='completed')return '<section class="segment">'+head+'<p class="empty">报告完成后才能做分段复盘。</p></section>';
 if(!run.approved)return '<section class="segment">'+head+'<p class="empty">分段复盘只使用已复核报告的冻结底稿；请先完成负责人复核。</p></section>';
 if(state==='queued'||state==='running'){const stage=Math.max(0,Math.min(3,seg.stage??0));return '<section class="segment">'+head+`<p class="segment-progress">搜索进行中 · ${esc(STAGE_NAMES[stage]||'准备中')} · 已完成 ${Math.max(0,seg.stage+1)}/4 个阶段</p><div class="segment-bar"><i style="width:${Math.max(8,(seg.stage+1)/4*100)}%"></i></div></section>`}
 if(state==='idle'||state==='failed'){return '<section class="segment">'+head+(state==='failed'?`<p class="error">搜索失败：${esc(seg.error||'未知错误')}</p>`:'')+`<p class="empty">尚未生成分段复盘。</p><button class="segment-start" data-id="${run.id}" ${canStart?'':'disabled'}>${state==='failed'?'重新搜索':'生成分段复盘'}</button></section>`}
 const r=seg.result,chosen=r.chosen;
 const summary=`<div class="metrics"><div><small>样本单数</small><strong>${fmt(r.orders)}</strong></div><div><small>超时单数</small><strong>${fmt(r.overdue)}</strong></div><div><small>开单日期</small><strong>${fmt(r.days)}</strong></div><div><small>切点</small><strong>${chosen?chosen.cuts_count:0} 处</strong></div><div><small>总平方误差</small><strong>${chosen?fmt(Number(chosen.cost.toFixed(4))):'不可行'}</strong></div></div>`;
 const cutline=chosen&&chosen.cuts.length?`<p class="segment-cuts">切点日期：${chosen.cuts.map(d=>'<code>'+esc(d)+'</code>').join(' → ')}</p>`:'';
 const reason=r.explanation?`<p class="warning">${esc(r.explanation)}</p>`:'';
 const groupRows=[];(r.segments||[]).forEach(s=>{s['分组'].forEach((g,i)=>groupRows.push({'段':i===0?s['序号']:'', '区间':i===0?s['开始日期']+' → '+s['结束日期']:'', '小组':g['小组'],'样本数':g['样本数'],'超时数':g['超时数'],'超时率':pct(g['超时率'])}))});
 const segTable=r.segments?.length?table(r.segments.map(s=>({'段':s['序号'],'开始日期':s['开始日期'],'结束日期':s['结束日期'],'样本数':s['样本数'],'超时数':s['超时数'],'超时率':pct(s['超时率'])}))):'';
 const p=chosen?`<p class="segment-meta">约束：最多 ${r.max_cuts} 个切点，每段每组至少 ${r.min_count} 单；时区 ${esc(r.timezone)}；小组 ${esc((r.groups||[]).join('、'))}</p>`:'';
 return '<section class="segment">'+head+summary+reason+cutline+p+(segTable?`<h4>分段汇总</h4>${segTable}<h4>每段各组样本数与超时数</h4>${table(groupRows)}`:'')+`<button class="segment-start" data-id="${run.id}" ${canStart?'':'disabled'}>重新搜索</button></section>`}
