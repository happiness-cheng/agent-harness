'use strict';
const $ = id => document.getElementById(id);
const state = {csrf: '', session: null, sessions: [], saved: null, signature: '', artifact: null, polling: false, instance: null, closing: false};
const labels = {ready:'就绪',running:'执行中',completed:'已结束',waiting_user:'等待确认',partial:'已暂停',failed:'执行失败',cancelled:'已停止'};
const node = (tag, text, cls) => {const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el;};
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4500);}
async function api(path, data){
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Harness-CSRF':state.csrf},body:data===undefined?undefined:JSON.stringify(data),credentials:'same-origin'});
  let value;try{value=await response.json();}catch{throw new Error('服务暂时不可用');}
  if(!response.ok){const map={access_or_confirmation_refused:'当前操作不属于本会话，或需要定向确认。',task_state_conflict:'当前任务状态不允许该操作，请等待刷新。',invalid_request:'请检查输入内容与时间格式。',csrf_refused:'页面验证已失效，请刷新后重试。',authentication_required:'请刷新页面重新连接。'};throw new Error(map[value.error]||'操作未完成，请检查任务状态。');}
  return value;
}
const path = suffix => `/api/sessions/${state.session}${suffix||''}`;
async function refreshSessions(){state.sessions=await api('/api/sessions');renderSessions();}
function renderSessions(){
  $('session-count').textContent=String(state.sessions.length).padStart(2,'0');$('session-list').replaceChildren();
  for(const item of state.sessions){const button=node('button',undefined,'session-item'+(item.session_id===state.session?' active':''));button.append(node('span',item.label));button.append(node('small',item.unread?`${item.unread} 未读`:labels[item.status]||'就绪'));button.addEventListener('click',()=>selectSession(item.session_id));$('session-list').append(button);}
}
async function selectSession(identity){state.session=identity;state.signature='';document.body.classList.remove('show-nav');history.replaceState(null,'',`/?session=${identity}`);renderSessions();await poll();}
async function createSession(){const item=await api('/api/sessions',{label:`会话 ${String(state.sessions.length+1).padStart(2,'0')}`});await refreshSessions();await selectSession(item.session_id);$('message').focus();}
function readJSON(text){try{return JSON.parse(text);}catch{return null;}}
function findReferences(value, output=new Set(), depth=0){if(depth>8||value===null)return output;if(typeof value==='object'){if(typeof value.artifact_id==='string')output.add(value.artifact_id);for(const item of Object.values(value))findReferences(item,output,depth+1);}else if(typeof value==='string'&&value.startsWith('{'))findReferences(readJSON(value),output,depth+1);return output;}
function renderMessages(saved){
  const area=$('messages');if(!saved.messages.length){if(!area.querySelector('.welcome')){const welcome=node('div',undefined,'welcome');welcome.append(node('div','h.','welcome-symbol'),node('h2','把事情，一步步做好。'),node('p','从一个问题开始，或交给助手一项明确的任务。'));area.replaceChildren(welcome);}return;}
  const atBottom=area.scrollHeight-area.scrollTop-area.clientHeight<100;const fragment=document.createDocumentFragment();
  for(const message of saved.messages){
    const item=node('article',undefined,`message ${message.role}`);item.append(node('div',message.role==='user'?'YOU':message.role==='tool'?'工具结果':'HARNESS','message-label'));
    if(message.role==='tool'){
      const parsed=readJSON(message.content);const details=node('details');details.append(node('summary',`${message.tool_call_id} · ${parsed?.ok===true?'已返回':parsed?.error?.execution_state==='not_started'?'未执行':'结果待核对'}`));
      details.append(node('pre',message.content.length>6000?message.content.slice(0,6000)+'\n[预览截断]':message.content));item.append(details);
      for(const identity of findReferences(parsed)){const button=node('button','查看原文 ↗','reference-button');button.addEventListener('click',()=>showArtifact(identity));item.append(button);}
    }else{
      const parsed=readJSON(message.content);
      if(message.role==='assistant'&&parsed&&['achievements','pending','blockers','next_steps'].every(key=>Array.isArray(parsed[key]))){
        const body=node('div',undefined,'message-body');body.append(node('small','检查点 · 模型整理，待核对'));
        for(const [key,label] of Object.entries({achievements:'已有成果',pending:'未完成项',blockers:'阻塞',next_steps:'建议下一步'})){if(parsed[key].length){body.append(node('strong',label));body.append(node('p',parsed[key].join('\n')));}}item.append(body);
      }else{item.append(node('div',message.content||((message.tool_calls||[]).length?'正在调用工具…':''),'message-body'));}
      if(message.role==='assistant')for(const id of new Set(Array.from(message.content.matchAll(/\[memory:([^\]\s]+)\]/g),m=>m[1]))){
        const button=node('button','核对记忆来源 ↗','reference-button');button.addEventListener('click',()=>showMemoryEvidence(id));item.append(button);
      }
    }
    fragment.append(item);
  }
  area.replaceChildren(fragment);if(atBottom||!state.signature)area.scrollTop=area.scrollHeight;
}
function renderTask(saved){
  const area=$('task-detail');area.replaceChildren();const work=saved.work_state;const proposal=work.proposal;
  if(proposal){area.append(node('h3',proposal.goal.objective));const list=node('ul');for(const text of proposal.goal.completion_criteria)list.append(node('li',text));area.append(list);const details=node('details');details.append(node('summary',`执行范围 · ${proposal.goal.actions.length} 项动作`),node('pre',JSON.stringify(proposal.goal.actions,null,2)));area.append(details);
    if(work.confirmed_goal_id!==proposal.proposal_id){area.append(node('p',work.mode==='plan'?'当前为 Plan，确认后也不会写入。':'当前为 Build，确认后仅执行列出的范围。'));const approve=node('button','确认目标并继续','primary');approve.disabled=saved.task?.status==='running';approve.addEventListener('click',()=>act(()=>api(path('/confirm'),{proposal_id:proposal.proposal_id,continue_task:true})));area.append(approve);}
  }else{area.append(node('p','确认目标后，进度会显示在这里。','muted'));}
  const cp=saved.checkpoint;if(cp&&cp.resumed_by===null){area.append(node('p',cp.checkpoint.model_note?.pending?.join('；')||'已有结果已保存，请核对后继续。'));const resume=node('button','继续本任务','secondary');resume.addEventListener('click',()=>resumeTask(saved));area.append(resume);}
  else if(saved.task&&['waiting_user','partial','failed','cancelled'].includes(saved.task.status)&&(!proposal||work.confirmed_goal_id===proposal.proposal_id)){const resume=node('button','补充信息后继续','secondary');resume.addEventListener('click',()=>resumeTask(saved));area.append(resume);}
  if(saved.error)area.append(node('p','运行已保护性暂停，需重新加载后台状态。','muted'));
}
async function resumeTask(saved){const text=$('message').value.trim()||'继续已确认任务，先核对已有结果。';const cp=saved.checkpoint;await act(()=>api(path('/resume'),{checkpoint_id:cp&&cp.resumed_by===null?cp.checkpoint.checkpoint_id:null,task_id:saved.task?.task_id||null,text}));$('message').value='';}
function renderItems(saved){
  const todos=$('todos');todos.replaceChildren();if(!saved.todos.length)todos.append(node('p','还没有待办。','muted'));
  for(const view of saved.todos){const item=view.item;const row=node('div',undefined,'todo-row'+(item.status==='completed'?' completed':''));const title=node('div',item.title,'todo-title');if(item.due_date)title.append(node('small',`${item.due_date}${view.overdue?' · 已逾期':''}`));row.append(node('span',item.status==='completed'?'✓':'○','todo-check'),title);todos.append(row);}
  const reminders=$('reminders');reminders.replaceChildren();for(const item of saved.reminders){if(item.status==='active')reminders.append(node('div',`◷ ${item.title} · ${new Date(item.scheduled_at).toLocaleString('zh-CN',{timeZone:item.time_zone})}`,'reminder-row'));}
  const notifications=$('notifications');notifications.replaceChildren();$('unread-count').textContent=saved.notifications.filter(item=>item.unread).length;
  if(!saved.notifications.length)notifications.append(node('p','重要的事，不遗漏。','muted'));
  for(const item of saved.notifications.slice().reverse()){const button=node('button',undefined,'notification'+(item.unread?' unread':''));button.append(node('span',item.title));button.append(node('small',`${item.unread?'未读':'已读'}${item.late_seconds?' · 延迟补发':''} · 桌面${({accepted:'接口已接受',failed:'发送失败',unknown:'结果未知',disabled:'未启用',pending:'待发送',attempting:'发送中'})[item.desktop.status]||item.desktop.status}`));button.addEventListener('click',()=>act(()=>api(path(`/notifications/${item.id}/read`),{})));notifications.append(button);}
  const budget=$('budget');budget.replaceChildren();if(saved.budget){for(const [key,label] of [['model_calls','模型调用'],['tool_calls','工具调用']]){const row=node('div',undefined,'budget-row');row.append(node('span',label),node('b',`${saved.budget.usage[key]} / ${saved.budget.limits[key]}`));budget.append(row);}budget.append(node('p','继续执行会保留累计用量。','muted'));}else budget.append(node('p','任务启动后开始累计。','muted'));
}
function render(saved){
  $('session-title').textContent=saved.label;$('task-status').textContent=labels[saved.task?.status]||'就绪';$('stop').disabled=!saved.task||saved.task.status!=='running';$('mode-plan').classList.toggle('selected',saved.work_state.mode==='plan');$('mode-build').classList.toggle('selected',saved.work_state.mode==='build');$('mode-caption').textContent=saved.work_state.mode==='plan'?'先讨论，再执行':'按确认范围执行';$('allow-web').checked=saved.allow_web;$('demo-banner').hidden=!saved.demo;
  const queued=saved.events.queue.length;const waiting=saved.task&&['partial','waiting_user','cancelled','failed'].includes(saved.task.status);$('queue-status').hidden=!queued&&!waiting;$('queue-status').textContent=waiting?`当前任务已暂停。${queued?`${queued} 条普通消息保留在队列。`:''}请用右侧确认或继续按钮恢复。`:`${queued} 条消息排队中，当前任务结束后处理。`;
  renderMessages(saved);renderTask(saved);renderItems(saved);$('trace').textContent=saved.trace.length?JSON.stringify(saved.trace,null,2):'暂无记录';
  const memory=saved.long_memory||{};$('allow-memory').checked=!!memory.enabled;
  $('allow-memory').disabled=saved.task?.status==='running';$('memory-scenario').disabled=!memory.enabled||saved.task?.status==='running';
  $('memory-scenario').replaceChildren(...[['','通用情景'],...(memory.scenarios||[]).map(s=>[s.id,s.label])].map(([value,label])=>{const option=node('option',label);option.value=value;return option;}));
  $('memory-scenario').value=memory.scenario_ids?.[0]||'';
  const receipt=memory.receipt;
  $('memory-status').textContent=!memory.enabled?'长期记忆已关闭':receipt?.status==='delivered'?`最近送达${receipt.delivered.length}项 · 遗漏${receipt.omitted.length}项`:receipt?.status==='delivery_unknown'?'记忆送达结果未知':'按需读取已确认历史';
  $('memory-detail').textContent=JSON.stringify({mode:memory.mode,receipt:receipt||null,citation:memory.citation||null},null,2);
}
async function poll(){if(!state.session||state.polling)return;state.polling=true;try{const saved=await api(path());const signature=JSON.stringify(saved);if(signature!==state.signature){render(saved);state.signature=signature;state.saved=saved;}$('connection-status').textContent='后台已连接';}catch(error){$('connection-status').textContent='连接中断，等待恢复';}finally{state.polling=false;}}
async function act(callback){try{await callback();await poll();await refreshSessions();}catch(error){toast(error.message);}}
async function showArtifact(identity,start=0){try{const value=await api(path(`/artifacts/${encodeURIComponent(identity)}?start=${start}&limit=4000`));state.artifact=value;$('artifact-source').textContent=`${value.source||'工具原文'} · ${value.start}–${value.end}`;$('artifact-content').textContent=value.text;$('artifact-next').disabled=!value.truncated;if(!$('artifact-dialog').open)$('artifact-dialog').showModal();}catch(error){toast(error.message);}}
async function showMemoryEvidence(identity){try{const value=await api(path(`/memory/evidence/${encodeURIComponent(identity)}`));state.artifact=null;$('artifact-source').textContent=`历史${value.role}陈述 · ${value.time} · ${value.source}`;$('artifact-content').textContent=value.quotes.map(q=>q.text).join('\n');$('artifact-next').disabled=true;if(!$('artifact-dialog').open)$('artifact-dialog').showModal();}catch(error){toast('记忆来源未送达、已撤回或不在当前情景。请重新检索核对。');}}
$('new-session').addEventListener('click',()=>act(createSession));$('mobile-nav').addEventListener('click',()=>document.body.classList.toggle('show-nav'));
$('mode-plan').addEventListener('click',()=>act(()=>api(path('/mode'),{mode:'plan'})));$('mode-build').addEventListener('click',()=>act(()=>api(path('/mode'),{mode:'build'})));
$('allow-web').addEventListener('change',()=>act(()=>api(path('/web-permission'),{enabled:$('allow-web').checked})));
$('allow-memory').addEventListener('change',()=>act(()=>api(path('/memory-permission'),{enabled:$('allow-memory').checked})));
$('memory-scenario').addEventListener('change',()=>act(()=>api(path('/memory-permission'),{enabled:$('allow-memory').checked,scenario_ids:$('memory-scenario').value?[$('memory-scenario').value]:[]})));
$('shutdown-service').addEventListener('click',async()=>{if(!confirm('关闭Adrian服务？会停止当前任务；会话会保留。'))return;try{await api('/api/adrian/shutdown',{instance_id:state.instance});state.closing=true;$('connection-status').textContent='正在关闭服务，请等待服务窗口退出';$('send').disabled=true;$('shutdown-service').disabled=true;toast('服务正在关闭；下次可双击桌面Adrian启动。');}catch(error){toast(error.message);}});
$('stop').addEventListener('click',()=>act(()=>api(path('/control'),{kind:'stop'})));
$('send-mode').addEventListener('change',()=>$('scope-label').hidden=$('send-mode').value!=='steer');
$('composer').addEventListener('submit',async event=>{event.preventDefault();const text=$('message').value.trim();if(!text||!state.session)return;$('send').disabled=true;try{if($('send-mode').value==='steer'){if(!state.saved?.task||state.saved.task.status!=='running')throw new Error('当前没有可引导的运行任务。');await api(path('/control'),{kind:'steer',task_id:state.saved.task.task_id,text,scope_changed:$('scope-changed').checked});}else await api(path('/input'),{text});$('message').value='';await poll();await refreshSessions();}catch(error){toast(error.message);}finally{$('send').disabled=false;}});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}});
$('close-artifact').addEventListener('click',()=>$('artifact-dialog').close());$('artifact-next').addEventListener('click',()=>{if(state.artifact)showArtifact(state.artifact.artifact_id,state.artifact.end);});
(async()=>{try{const initial=await api('/api/bootstrap');state.csrf=initial.csrf;state.sessions=initial.sessions;state.instance=initial.instance_id;$('shutdown-service').hidden=!initial.can_shutdown;renderSessions();const requested=new URLSearchParams(location.search).get('session');if(state.sessions.length)await selectSession(state.sessions.some(item=>item.session_id===requested)?requested:state.sessions[0].session_id);else await createSession();setInterval(()=>{if(!state.closing)poll();},1500);setInterval(()=>{if(!state.closing)refreshSessions().catch(()=>{});},5000);}catch(error){toast('无法连接本地后台，请检查服务是否已启动。');}})();
