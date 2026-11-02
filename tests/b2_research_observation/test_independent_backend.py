"""A反例确认：使用生产WebReader，只有fetch替换为合成计数后端。"""
from uuid import UUID
import pytest
import min_agent.application as application
from min_agent.research import ResearchService
from min_agent.sessions import ExecutionContext,SessionStore
from min_agent.storage import SQLiteStorage,VersionConflict
from test_independent_boundaries import build,b

@pytest.mark.parametrize('enabled',[True,False])
def test_production_reader_does_not_fetch_after_durable_revocation(tmp_path,monkeypatch,enabled):
 app,sid=build(tmp_path,monkeypatch,real_reader=True,enabled=enabled)
 ctx=app.context(sid);events=[]
 class Fetcher:
  calls=0
  def fetch(self,url):
   self.calls+=1
   events.append(dict(boundary='production WebReader entered fetch',allow_web=app.storage.load(ctx).snapshot.extensions['allow_public_web']))
   return dict(text=b.CONTENT,url=url,content_type='text/plain',truncated=False)
  def close(self):pass
 fetcher=Fetcher();app._fetchers[ctx.session_id]=fetcher
 class Probe(ResearchService):
  def run(self,*args,**kwargs):
   original=kwargs['permission_check'];first=True
   def guard():
    nonlocal first
    if first:
     first=False;peer=SQLiteStorage.open(tmp_path/'case.sqlite3')
     try:
      writer=SessionStore(storage=peer);writer.set_extension(ctx,'allow_public_web',False)
      events.append(dict(boundary='committed revoke before production permission_check',allow_web=peer.load(ctx).snapshot.extensions['allow_public_web']))
     finally:peer.close()
    return original()
   kwargs['permission_check']=guard
   return super().run(*args,**kwargs)
 monkeypatch.setattr(application,'ResearchService',Probe)
 try:
  try:b.run_research(app,sid)
  except VersionConflict as exc:events.append(dict(exception='VersionConflict',message=str(exc)))
  snap=app.storage.load(ctx).snapshot
  assert snap.extensions['allow_public_web'] is False
  assert events[0]['allow_web'] is False
  assert fetcher.calls==0,f'production WebReader called synthetic fetch {fetcher.calls} times after committed revoke'
 finally:app.close()
