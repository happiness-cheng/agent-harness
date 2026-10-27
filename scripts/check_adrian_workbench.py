"""隔离数据的浏览器验收；仅启动脚本Model，不读取凭据或调用模型API。"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    output=args.output_dir.resolve()
    with socket.socket() as stream:
        stream.bind(('127.0.0.1',0));port=stream.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    options=dict(cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT/'src')))
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    with (output/'server.txt').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','tests.fakes.adrian_ui_server',
            '--database',str(output/'workspace.sqlite3'),'--port',str(port)],stdout=log,stderr=subprocess.STDOUT,**options)
        try:
            with httpx.Client(base_url=url,timeout=3,trust_env=False) as client:
                end=time.monotonic()+25;identity=None
                while time.monotonic()<end:
                    if process.poll() is not None:raise RuntimeError('隔离服务退出，见server.txt')
                    try:
                        response=client.get('/_adrian/identity')
                        if response.status_code==200:identity=response.json();break
                    except httpx.HTTPError:pass
                    time.sleep(.1)
                assert identity and identity['provider']=='custom'
                with sync_playwright() as playwright:
                    installed=Path(playwright.chromium.executable_path)
                    browser=playwright.chromium.launch(headless=True) if installed.is_file() else playwright.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(url);page.wait_for_load_state('networkidle')
                    page.locator('#session-list button').filter(has_text='跨会话回忆验收').click()
                    expect(page.locator('#allow-memory')).to_be_checked()
                    page.locator('#message').fill('请回忆我的Python讲解偏好')
                    page.locator('#send').click()
                    button=page.get_by_role('button',name='核对记忆来源 ↗')
                    button.wait_for(timeout=20000);button.click()
                    page.locator('#artifact-dialog').wait_for(state='visible')
                    assert 'Python' in page.locator('#artifact-content').inner_text()
                    page.locator('#close-artifact').click()
                    csrf=client.get('/api/bootstrap').json()['csrf']
                    sessions=client.get('/api/sessions').json()
                    target=next(s['session_id'] for s in sessions if s['label']=='跨会话回忆验收')
                    saved=client.get('/api/sessions/'+target).json()
                    assert saved['task']['status']=='completed'
                    assert len(saved['long_memory']['receipt']['delivered'])==1
                    assert saved['long_memory']['citation']['status']=='valid'
                    page.locator('#allow-memory').uncheck()
                    page.get_by_text('长期记忆已关闭',exact=True).wait_for()
                    page.locator('#message').fill('普通任务')
                    page.locator('#send').click()
                    page.get_by_text('普通任务离线答复',exact=True).wait_for(timeout=20000)
                    page.screenshot(path=str(output/'workbench.png'),full_page=True)
                    assert not errors,errors
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#shutdown-service').click()
                    process.wait(timeout=15)
                    assert process.returncode==0
                    browser.close()
                result=dict(backend='SCRIPTED_MODEL_ACTUAL_HARNESS',external_model_requests=0,
                    cross_session_retrieval=True,source_dialog=True,memory_switch=True,ordinary_task=True,
                    citation_status='valid',browser_errors=errors,graceful_shutdown=True,
                    database=str(output/'workspace.sqlite3'),screenshot=str(output/'workbench.png'))
                (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False))
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
