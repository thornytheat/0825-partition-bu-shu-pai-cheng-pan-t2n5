import {request, element} from './client.js';
import {snapshot, history} from './view.js';
import {visualize} from './plots.js';

const form = document.querySelector('#source');
const file = document.querySelector('#file');
const profile = document.querySelector('#profile');
const status = document.querySelector('#notice');
const detail = document.querySelector('#review');
const evidence = document.querySelector('#evidence');
const stages = [...document.querySelectorAll('[data-stage]')];
const criticalTools = document.querySelector('#critical-tools');
const quotaSummary = document.querySelector('#quota-summary');
const reservationForm = document.querySelector('#reservation-form');
const boardNotice = document.querySelector('#board-notice');
let stage = 'balance';
let report, selected, batch, running = false;

function boardMessage(text, failure = false) {
  if (!boardNotice) return;
  boardNotice.textContent = text || '';
  boardNotice.dataset.failure = String(failure);
}

function activeCase() {
  return report?.cases?.find(row => row.case === selected) || report?.cases?.[0];
}

function drawBoard() {
  if (!criticalTools) return;
  criticalTools.replaceChildren();
  const item = activeCase();
  const measure = item?.measure;
  if (quotaSummary) {
    quotaSummary.textContent = measure
      ? `已排 ${measure.scheduled ?? measure.value} / 配额 ${measure.quota ?? measure.expected}` +
        ` · 关键 ${measure.critical_scheduled ?? 0}/${measure.critical_total ?? 0}`
      : '';
  }
  if (!measure?.task_ids?.length) {
    criticalTools.append(element('span', '执行批次后可在此标出关键分区', {class: 'board-hint'}));
    return;
  }
  const critical = new Set(measure.critical_ids || []);
  for (const id of measure.task_ids) {
    const button = element('button', `分区 ${id}${critical.has(id) ? ' · 关键' : ''}`, {
      type: 'button', 'data-task': id, 'aria-pressed': String(critical.has(id)),
      class: critical.has(id) ? 'critical-on' : '',
    });
    criticalTools.append(button);
  }
}

async function toggleCritical(taskId) {
  if (!batch) { boardMessage('请先执行并保存批次', true); return; }
  const caseId = activeCase().case;
  const measure = activeCase()?.measure;
  const current = new Set(measure?.critical_ids || []);
  if (current.has(taskId)) current.delete(taskId); else current.add(taskId);
  // 仅提交当前分组的标记，按分组映射，避免跨业务组同编号误标。
  const payload = {revision: batch.revision, critical_by_case: {[caseId]: [...current]}};
  await perform(async () => {
    try {
      const result = await request(`/api/runs/${batch.id}/critical`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      stage = 'balance'; show(result, true); selected = caseId; draw();
      boardMessage('关键分区已更新并重排');
      await refresh();
    } catch (error) {
      boardMessage(error.message, true);
      if (error.code === 'STALE_REVIEW') await reloadBatch();
    }
  });
}

async function submitReservation(event) {
  event.preventDefault();
  if (!batch) { boardMessage('请先执行并保存批次', true); return; }
  const data = new FormData(reservationForm);
  const release = Number(data.get('release'));
  const deadline = Number(data.get('deadline'));
  if (!Number.isInteger(release) || !Number.isInteger(deadline) || deadline <= release) {
    boardMessage('请填写有效的预留起止时隙（止 > 起）', true); return;
  }
  await perform(async () => {
    try {
      const result = await request(`/api/runs/${batch.id}/reschedule`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({revision: batch.revision, reservations: [{release, deadline}]}),
      });
      stage = 'balance'; show(result, true); boardMessage('已按新预留重算，旧签发资格失效');
      await refresh();
    } catch (error) {
      boardMessage(error.message, true);
      if (error.code === 'STALE_REVIEW') await reloadBatch();
    }
  });
}

async function reloadBatch() {
  if (!batch) return;
  const result = await request(`/api/runs/${batch.id}`);
  stage = 'balance'; show(result, true);
}

function source() {
  const data = new FormData();
  if (file.files.length) data.append('file',file.files[0]);
  else data.append('sample',profile.value);
  return data;
}

function announce(text, failure = false) {
  status.textContent = text;
  status.dataset.failure = String(failure);
}

function draw() {
  if (!report) return;
  snapshot(report, selected, stage);
  visualize(report, selected);
  drawBoard();
  for (const button of stages) button.setAttribute('aria-pressed',String(button.dataset.stage===stage));
  document.querySelector('#panel-title').textContent = stages.find(button => button.dataset.stage===stage)?.textContent || '运行对账';
  detail.hidden = !batch;
  if (batch) {
    document.querySelector('#run-status').textContent = `${batch.id.slice(0,10)} · ${batch.status} · r${batch.revision}`;
    detail.querySelector('[value="APPROVED"]').disabled = !report.balance.releasable || batch.status==='APPROVED';
    detail.querySelector('[value="HELD"]').disabled = batch.status==='APPROVED';
    evidence.href = `/api/runs/${batch.id}/evidence`;
  }
}

function show(payload, committed = false) {
  batch = committed ? payload : null;
  report = committed ? payload.report : payload;
  detail.elements.note.value = batch?.note || '';
  selected = report.cases?.[0]?.case;
  draw();
}

async function refresh() {
  const data = await request('/api/runs');
  history(data.items, batch?.id);
}

async function perform(action) {
  if (running) return;
  running = true;
  document.body.setAttribute('aria-busy','true');
  announce('处理中…');
  try { await action(); }
  catch (error) { announce(error.message,true); }
  finally { running = false; document.body.removeAttribute('aria-busy'); }
}

function preview(next) {
  return perform(async () => {
    const payload = await request(`/api/stages/${next}`,{method:'POST',body:source()});
    stage = next;
    show(payload);
    announce('阶段预览已更新');
    await refresh();
  });
}

stages.forEach(button => button.addEventListener('click',()=>preview(button.dataset.stage)));
form.addEventListener('submit', event => {
  event.preventDefault();
  perform(async () => {
    const result = await request('/api/runs',{method:'POST',body:source()});
    stage = 'balance'; show(result,true);
    announce('批次已保存'); await refresh();
  });
});
const filename = document.querySelector('#file-name');
profile.addEventListener('change',()=>{file.value=''; filename.textContent=''; preview(stage);});
file.addEventListener('change',()=>{filename.textContent=file.files[0]?.name || ''; preview(stage);});
document.querySelector('#rows').addEventListener('click',event=>{
  const button = event.target.closest('[data-case]');
  if (button) { selected = button.dataset.case; draw(); document.querySelector(`[data-case="${CSS.escape(selected)}"]`).focus(); }
});
document.querySelector('#history').addEventListener('click',event=>{
  const button = event.target.closest('[data-run]');
  if (button) perform(async()=>{
    const result = await request(`/api/runs/${button.dataset.run}`);
    stage='balance'; show(result,true); announce('已载入历史批次'); await refresh();
  });
});
detail.addEventListener('submit',event=>{
  event.preventDefault();
  perform(async()=>{
    const payload={status:event.submitter.value,revision:batch.revision,note:detail.elements.note.value};
    try {
      const result=await request(`/api/runs/${batch.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      show(result,true); announce(result.status==='APPROVED'?'批次已签发':'批次已暂缓'); await refresh();
    } catch (error) {
      announce(error.message,true);
      if (error.code==='STALE_REVIEW' || error.code==='STALE_SCHEDULE') await reloadBatch();
      throw error;
    }
  });
});
criticalTools?.addEventListener('click',event=>{
  const button=event.target.closest('[data-task]');
  if (button) toggleCritical(button.dataset.task);
});
reservationForm?.addEventListener('submit',submitReservation);
preview(stage);
