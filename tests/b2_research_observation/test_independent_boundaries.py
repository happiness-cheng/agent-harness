"""A独立边界验收；只用合成SQLite、假模型/网络。复用B的假后端工具，不复用B判据。"""
from pathlib import Path
import importlib.util,json
import pytest
from min_agent.storage import SQLiteStorage
from min_agent.sessions import SessionStore
from min_agent.research_observation import ResearchObservationStore
from min_agent.models import ModelResponse
from min_agent.research import ResearchService
from min_agent.tools.read_webpage import WebReader
import min_agent.application as application

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('b2_synthetic_helpers',HERE/'test_research_observation.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
def record(name,app,sid,**extra):
 # A独立反例现保存在项目阶段记录中；自动回归只断言，不生成可变证据文件。
 return None

@pytest.fixture(autouse=True)
def reset():
 b.FakeSearch.calls=0;b.FakeSearch.revoke=None;b.FakeReader.calls=0;b.FakeReader.fail=False
 b.FakeReader.save_text=b.CONTENT;b.FakeReader.range_start=0;b.FakeReader.range_end=b.LENGTH

def build(tmp_path,monkeypatch,*,enabled=True,real_reader=False):
 app,sid=b.build(tmp_path,monkeypatch,[],enabled=enabled)
 if real_reader:
  monkeypatch.setattr(application,'WebReader',WebReader)
  class Fetcher:
   def fetch(self,url):return dict(text=b.CONTENT,url=url,content_type='text/plain',truncated=False)
   def close(self):pass
  app._fetchers[app.context(sid).session_id]=Fetcher()
 class Dynamic(b.Model):
  def generate(self,messages,*,tools=None):
   self.count+=1
   if self.count==1:return b.tool('p','research',{'question':'synthetic public question'})
   if self.count==2:return b.tool('r','read_webpage',{'url':'https://example.com/fact'})
   if self.count==3:
    items=app.sessions.extension(app.context(sid),'artifacts',{})
    if items:return b.evidence(artifact=next(reversed(items)))
    return ModelResponse(finish_reason='stop',content='{}')
   return ModelResponse(finish_reason='stop',content='done')
 app.model=Dynamic([])
 return app,sid

def test_authoritative_read_failure_withholds_warm_verified(tmp_path,monkeypatch):
 app,sid=build(tmp_path,monkeypatch)
 try:
  b.run_research(app,sid);before=app.get_research_observation(sid);assert before.status=='VERIFIED'
  def unavailable(*args,**kwargs):raise OSError('A_SYNTHETIC_AUTHORITY_UNAVAILABLE')
  with monkeypatch.context() as fault:
   fault.setattr(app.storage,'load',unavailable)
   actual=app.get_research_observation(sid)
  record('authority-read-failure',app,sid,before=before.model_dump(mode='json'),actual=actual.model_dump(mode='json'),expected='WITHHELD')
  assert actual.status=='WITHHELD',f'authoritative read unavailable but host returned {actual.status}'
  assert app.get_research_observation(sid).status=='WITHHELD'
 finally:app.close()

def test_stale_artifact_after_separate_legitimate_writer(tmp_path,monkeypatch):
 app,sid=build(tmp_path,monkeypatch);peer=None
 try:
  b.run_research(app,sid);before=app.get_research_observation(sid);assert before.status=='VERIFIED'
  peer=SQLiteStorage.open(tmp_path/'case.sqlite3');writer=SessionStore(storage=peer);context=app.context(sid)
  writer.set_extension(context,'artifacts',{})
  current=ResearchObservationStore(writer).get(context,before.run_ref);actual=app.get_research_observation(sid,before.run_ref)
  assert current.status=='WITHHELD';assert peer.load(context).snapshot.extensions['artifacts']=={}
  record('stale-artifact',app,sid,before=before.model_dump(mode='json'),fresh=current.model_dump(mode='json'),actual=actual.model_dump(mode='json'),expected='WITHHELD')
  assert actual.status=='WITHHELD',f'authoritative artifacts empty; stale host returned {actual.status}'
 finally:
  if peer:peer.close()
  app.close()

def test_new_parent_in_other_instance_does_not_reuse_old_verified(tmp_path,monkeypatch):
 app,sid=build(tmp_path,monkeypatch);other=None
 try:
  b.run_research(app,sid);before=app.get_research_observation(sid);assert before.status=='VERIFIED'
  other=application.HarnessApplication(tmp_path/'case.sqlite3',model=b.Model([ModelResponse(finish_reason='stop',content='new task')]),research_observation_enabled=True,memory_mode='off')
  oldtask=app.sessions.snapshot(app.context(sid)).task.task_id
  b.run_research(other,sid)
  newtask=other.sessions.snapshot(other.context(sid)).task.task_id;assert newtask!=oldtask
  fresh=other.get_research_observation(sid,before.run_ref);assert fresh.status=='WITHHELD'
  actual=app.get_research_observation(sid,before.run_ref)
  record('stale-parent',app,sid,old_parent=str(oldtask),new_parent=str(newtask),fresh=fresh.model_dump(mode='json'),actual=actual.model_dump(mode='json'),expected='WITHHELD')
  assert actual.status=='WITHHELD',f'new authoritative parent; stale host returned {actual.status}'
 finally:
  if other:other.close()
  app.close()

@pytest.mark.parametrize('enabled',[True,False])
def test_cross_instance_revocation_immediately_before_permission_guard(tmp_path,monkeypatch,enabled):
 app,sid=build(tmp_path,monkeypatch,enabled=enabled);events=[]
 class GuardBoundaryProbe(ResearchService):
  def run(self,*args,**kwargs):
   original=kwargs['permission_check'];first=True
   def before_guard():
    nonlocal first
    if first:
     first=False
     peer=SQLiteStorage.open(tmp_path/'case.sqlite3')
     try:
      writer=SessionStore(storage=peer);ctx=app.context(sid)
      writer.set_extension(ctx,'allow_public_web',False)
      events.append(dict(boundary='immediately before production read permission callback',allow_web=peer.load(ctx).snapshot.extensions['allow_public_web']))
     finally:peer.close()
    return original()
   kwargs['permission_check']=before_guard
   return super().run(*args,**kwargs)
 monkeypatch.setattr(application,'ResearchService',GuardBoundaryProbe)
 try:
  b.run_research(app,sid)
  context=app.context(sid) if sid else None
  # 禁止的后端调用发生后，CAS也可能使本地写入器失效；直接核对持久状态。
 except Exception as exc:
  events.append(dict(propagated_exception=type(exc).__name__,message=str(exc)))
 finally:
  # 即使SessionStore已失效，也用已知owner/session直接读取持久状态。
  from min_agent.sessions import ExecutionContext
  from uuid import UUID
  ctx=ExecutionContext(owner_id=app.owner_id,session_id=UUID(sid));snap=app.storage.load(ctx).snapshot
  observed=dict(backend_read_calls=b.FakeReader.calls,enabled=enabled,events=events,sqlite_snapshot=snap.model_dump(mode='json'))
  app.close()
 assert events and events[0]['allow_web'] is False
 assert b.FakeReader.calls==0,f'web permission durably revoked before check; backend entered {b.FakeReader.calls} times'

def test_real_reader_synthetic_fetcher_normal_chain(tmp_path,monkeypatch):
 pytest.importorskip('trafilatura')
 app,sid=build(tmp_path,monkeypatch,real_reader=True)
 try:
  b.run_research(app,sid);actual=app.get_research_observation(sid)
  record('real-reader',app,sid,actual=actual.model_dump(mode='json'))
  assert actual.status=='VERIFIED';assert actual.verified_citations==1
  assert actual.documents[0].end==b.LENGTH
 finally:app.close()

def test_query_is_readonly_and_retains_current_parent_after_restart(tmp_path,monkeypatch):
 app,sid=build(tmp_path,monkeypatch);other=None
 try:
  b.run_research(app,sid);ctx=app.context(sid);before=app.storage.load(ctx).snapshot.model_dump(mode='json');calls=app.model.count
  observation=app.get_research_observation(sid)
  for _ in range(3):assert app.get_research_observation(sid,observation.run_ref)==observation
  assert app.model.count==calls;assert app.storage.load(ctx).snapshot.model_dump(mode='json')==before
  other=application.HarnessApplication(tmp_path/'case.sqlite3',model=b.Model([]),research_observation_enabled=True,memory_mode='off')
  assert other.get_research_observation(sid,observation.run_ref)==observation
  record('query-readonly-restart',app,sid,actual=observation.model_dump(mode='json'),model_calls_unchanged=True,sqlite_unchanged=True)
 finally:
  if other:other.close()
  app.close()
