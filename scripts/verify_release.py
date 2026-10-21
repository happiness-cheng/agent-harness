"""从本地wheel缓存建立独立环境，验证包、工具循环、CLI与静态资源；不调用实网。"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


SMOKE = r'''
from pathlib import Path
import json,socket,subprocess,sys,time
from uuid import uuid4
import httpx
import min_agent
from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse,ToolCall
from min_agent.tools.calculator import calculate
from min_agent.tools.read_webpage import WebReader
from min_agent.sessions import SessionStore
from min_agent.artifacts import ArtifactStore
root=Path(sys.prefix)
assert Path(min_agent.__file__).is_relative_to(root)
assert calculate("0.1+0.2")=="0.3"
class Fetcher:
 def fetch(self,url):
  return {"url":url,"text":"<html><article><h1>Clean environment</h1><p>"+"Public synthetic paragraph. "*100+"</p></article></html>","truncated":False,"content_type":"text/html"}
store=SessionStore();context=store.create(uuid4())
assert WebReader(Fetcher(),ArtifactStore(store,context)).read("https://example.com",max_chars=2000)["text"]
class Model:
 def __init__(self):self.calls=0
 def generate(self,messages,**kwargs):
  self.calls+=1
  if self.calls==1:return ModelResponse(finish_reason="tool_calls",tool_calls=[ToolCall(id="clean",name="calculator",arguments={"expression":"0.1+0.2"})])
  return ModelResponse(finish_reason="stop",content="0.3")
app=HarnessApplication(root/"verification.sqlite3",model=Model())
identity=app.create_session()["session_id"];app.submit(identity,"计算0.1+0.2");app.wait(identity)
saved=app.snapshot(identity)
assert saved["task"]["status"]=="completed" and saved["messages"][-1]["content"]=="0.3"
assert saved["budget"]["usage"]["model_calls"]==2 and saved["budget"]["usage"]["tool_calls"]==1
app.close()
sock=socket.socket();sock.bind(("127.0.0.1",0));port=sock.getsockname()[1];sock.close()
log=(root/"server.log").open("w",encoding="utf-8")
process=subprocess.Popen([sys.executable,"-I","-m","min_agent.web","--demo","--database",str(root/"web.sqlite3"),"--port",str(port)],
 stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
try:
 with httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False,timeout=3) as client:
  for _ in range(100):
   if process.poll() is not None:raise RuntimeError("CLI early exit")
   try:response=client.get("/api/bootstrap");break
   except httpx.ConnectError:time.sleep(.1)
  else:raise RuntimeError("CLI not ready")
  assert response.status_code==200 and response.json()["demo"] is True
  headers={"x-harness-csrf":response.json()["csrf"]}
  identity=client.post("/api/sessions",json={"label":"Clean demo"},headers=headers).json()["session_id"]
  assert client.post(f"/api/sessions/{identity}/input",json={"text":"Synthetic demo"},headers=headers).status_code==202
  for _ in range(100):
   saved=client.get(f"/api/sessions/{identity}").json()
   if saved["task"] and saved["task"]["status"]=="completed":break
   time.sleep(.05)
  assert saved["messages"][-1]["role"]=="assistant"
  for resource in ["/","/static/app.js","/static/style.css"]:assert client.get(resource).status_code==200
finally:
 process.terminate();process.wait(timeout=15);log.close()
print(json.dumps({"isolated_import":min_agent.__file__,"calculator":"0.3","html_extraction":True,
 "application_tool_loop":True,"cli_http":True,"static_resources":True,"live_calls":0,"source_tree_on_path":False}))
'''


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    if result.returncode:
        print(result.stdout[-2000:]); print(result.stderr[-2000:])
        result.check_returncode()
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists(): raise FileExistsError("保留已有验证，不覆盖")
    root = Path(__file__).resolve().parents[1]
    target = Path(tempfile.mkdtemp(prefix="agent-harness-clean-release-"))
    python = target / "Scripts" / "python.exe"
    run([sys.executable, "-m", "venv", "--without-pip", str(target)])
    run([sys.executable, "-m", "pip", "--python", str(python), "--isolated", "install",
         "--disable-pip-version-check", "--no-index", "--find-links", str(args.wheelhouse.resolve()),
         "-r", str(root / "requirements.lock"), str(args.wheel.resolve())])
    smoke = json.loads(run([str(python), "-X", "utf8", "-I", "-c", SMOKE], cwd=target).strip())
    report = {"environment": str(target), "wheel": str(args.wheel.resolve()),
              "install_exit": 0, "smoke_exit": 0, "smoke": smoke}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
