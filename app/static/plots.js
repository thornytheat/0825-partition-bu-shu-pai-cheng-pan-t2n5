import {element} from './client.js';

function block(target,className,content) {
  const node=element('div',content,{class:className});
  target.append(node);
  return node;
}
const note=(target,text)=>block(target,'chart-note',text);

export function visualize(report,selected) {
  const target=document.querySelector('#visual');
  target.replaceChildren();
  const measurement=report.cases?.find(row=>row.case===selected)?.measure;
  if (!measurement) {block(target,'chart-note','选择有计算值的分组');return;}
  const e=measurement.evidence;

  const end=Math.max(1,...Object.values(e.schedule).map(v=>v[1]));
  for(const [task,[start,finish]] of Object.entries(e.schedule)) {
    const row=block(target,'timeline-row'); block(row,'legend',`任务 ${task}`);
    const rail=block(row,'timeline-base'); const interval=block(rail,'timeline-slot',`${start}–${finish}`);
    interval.style.left=`${start*100/end}%`; interval.style.width=`${(finish-start)*100/end}%`;
  }
  for(const [task,reason] of Object.entries(e.rejected)) note(target,`${task} · ${reason}`);
}
