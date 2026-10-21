from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
import socket
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from min_agent.tools.reminder import ReminderService
from min_agent.web.app import create_app
from tests.fakes.model import ScriptedModel


@contextmanager
def server(harness):
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(create_app(harness), log_level='error'))
    thread = Thread(target=lambda: service.run(sockets=[sock]), daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not service.started and thread.is_alive() and time.monotonic() < deadline: time.sleep(.02)
    assert service.started
    try: yield f'http://127.0.0.1:{port}'
    finally:
        service.should_exit = True; thread.join(timeout=15)


def test_browser_confirm_build_todo_two_sessions_notification_and_mobile(tmp_path):
    proposal = ModelResponse(finish_reason='tool_calls', tool_calls=[ToolCall(id='proposal', name='propose_goal', arguments={
        'objective': '记录合成周报', 'constraints': [], 'completion_criteria': ['保存一条真实待办'],
        'actions': [{'operation': 'todo.add', 'parameters': {'title': '合成周报', 'due_date': None}}],
    })])
    model = ScriptedModel([proposal, ModelResponse(finish_reason='tool_calls', tool_calls=[
        ToolCall(id='write', name='todo_add', arguments={'title': '合成周报'}),
    ]), ModelResponse(finish_reason='stop', content='合成周报已根据真实工具结果保存。')])
    harness = HarnessApplication(tmp_path/'browser.sqlite3', model=model)
    with server(harness) as base, sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]; page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(base); page.wait_for_load_state('networkidle')
        page.locator('#message').fill('请记录合成周报'); page.locator('#send').click()
        expect(page.locator('#task-status')).to_have_text('等待确认', timeout=15000)
        expect(page.locator('#todos')).to_contain_text('还没有待办')
        page.locator('#mode-build').click()
        page.get_by_role('button',name='确认目标并继续').click()
        expect(page.locator('#todos')).to_contain_text('合成周报',timeout=15000)
        expect(page.locator('#messages')).to_contain_text('合成周报已根据真实工具结果保存',timeout=15000)
        first = harness.list_sessions()[0]['session_id']
        context = harness.context(first)
        ReminderService(harness.sessions).add(context,'浏览器合成提醒',datetime(2020,1,1,tzinfo=UTC))
        harness.scheduler.tick()
        expect(page.locator('#notifications')).to_contain_text('浏览器合成提醒',timeout=10000)
        expect(page.locator('#unread-count')).to_have_text('1')
        page.locator('#notifications button').click()
        expect(page.locator('#unread-count')).to_have_text('0')
        page.locator('#new-session').click()
        expect(page.locator('#todos')).to_contain_text('还没有待办')
        page.locator('#session-list button').first.click()
        expect(page.locator('#todos')).to_contain_text('合成周报')
        page.reload(); expect(page.locator('#todos')).to_contain_text('合成周报',timeout=10000)
        page.screenshot(path=str(tmp_path/'desktop.png'),full_page=True,animations="disabled")
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        expect(page.locator(".sidebar")).not_to_be_in_viewport()
        page.locator("#mobile-nav").click()
        expect(page.locator(".sidebar")).to_be_in_viewport()
        page.locator("#session-list button").first.click()
        expect(page.locator(".sidebar")).not_to_be_in_viewport()
        page.screenshot(path=str(tmp_path/'mobile.png'),full_page=True,animations="disabled")
        assert errors == []
        browser.close()


def test_browser_queue_stop_and_directed_resume_keep_messages_separate(tmp_path):
    from threading import Event
    entered, release = Event(), Event()
    calls = []
    class Model:
        def generate(self, messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                entered.set(); assert release.wait(20)
            return ModelResponse(finish_reason="stop", content=f"合成响应{len(calls)}")
    harness = HarnessApplication(tmp_path / "controls.sqlite3", model=Model())
    try:
        with server(harness) as base, sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base); page.wait_for_load_state("networkidle")
            page.locator("#message").fill("当前任务"); page.locator("#send").click()
            assert entered.wait(10)
            page.locator("#message").fill("稍后处理的消息"); page.locator("#send").click()
            expect(page.locator("#queue-status")).to_contain_text("1 条")
            page.locator("#stop").click()
            release.set()
            expect(page.locator("#task-status")).to_have_text("已停止", timeout=15000)
            assert len(calls) == 1
            assert all("稍后处理的消息" not in message.content for message in calls[0])
            page.get_by_role("button", name="继续本任务").click()
            expect(page.locator("#messages")).to_contain_text("合成响应3", timeout=15000)
            assert len(calls) == 3
            expect(page.locator("#queue-status")).to_be_hidden()
            assert any(message.content == "稍后处理的消息" for message in calls[2])
            assert errors == []
            browser.close()
    finally:
        release.set(); harness.close()
