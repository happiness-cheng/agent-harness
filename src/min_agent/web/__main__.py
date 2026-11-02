"""以完整参数启动本地后台；默认离线，不隐式读取凭据或代理。"""

import argparse
import os
from pathlib import Path
from threading import Event, Thread

from min_agent.application import HarnessApplication
from min_agent.web.app import create_app


def _configuration_error(error):
    """把配置校验异常压成单行信息；不回显输入值。"""
    issues = getattr(error, "errors", None)
    if callable(issues):
        try:
            items = issues()
        except Exception:
            items = None
        if items:
            message = str(items[0].get("msg", "")).replace("Value error, ", "").strip()
            if message:
                return message
    return str(error).strip() or "Ollama配置无效"


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地Agent Harness工作台")
    parser.add_argument("--database", default="data/workspace.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--live", action="store_true", help="允许将用户请求发送至指定DeepSeek官方API")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--proxy", help="模型/搜索的显式本机HTTP代理；固定DNS网页读取直连，不读取环境")
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--desktop", action="store_true", help="启用Windows桌面通知；API接受不等于实际显示")
    parser.add_argument("--demo", action="store_true", help="显式离线演示，与--live互斥")
    parser.add_argument("--ollama", action="store_true", help="使用本机Ollama模型作为决策核心；不需要云端凭据")
    parser.add_argument("--ollama-model", default="qwen3.5:4b", help="本机已安装的Ollama模型名")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="只允许本机Ollama地址")
    parser.add_argument("--ollama-num-ctx", type=int, default=32768, help="Ollama上下文窗口(tokens)；低内存可用16384，范围2048-262144")
    parser.add_argument("--ollama-num-predict", type=int, default=1024, help="单次生成的最大输出tokens")
    parser.add_argument("--ollama-keep-alive", default="30m", help="模型驻留时间，例如30m")
    parser.add_argument("--ollama-timeout", type=float, default=180.0, help="单次本机模型请求超时秒数")
    parser.add_argument("--adrian", action="store_true", help="真实Adrian本地试用：按需Memory、单实例、就绪后浏览器")
    parser.add_argument("--no-browser", action="store_true", help="显式不自动打开浏览器")
    memory = parser.add_mutually_exclusive_group()
    memory.add_argument("--long-memory", dest="memory_mode", action="store_const", const="active")
    memory.add_argument("--no-long-memory", dest="memory_mode", action="store_const", const="off")
    options = parser.parse_args(argv)
    if options.adrian:
        if options.demo: parser.error("Adrian入口只使用真实模型，不能回退Demo")
        options.live = True
        if options.credentials_file is None:
            options.credentials_file = Path(os.environ.get('ADRIAN_CREDENTIALS_FILE') or Path.home()/'agent-harness-credentials.json')
        if options.proxy is None:
            value = os.environ.get('ADRIAN_PROXY','http://127.0.0.1:7890')
            options.proxy = None if value.lower()=='direct' else value
    if options.live and options.demo: parser.error("--live与--demo不能同时使用")
    if options.ollama and (options.live or options.demo):
        parser.error("--ollama不能与--live/--demo/--adrian同时使用")
    if not 1024 <= options.port <= 65535: parser.error("端口需为1024到65535")
    if not 1 <= options.max_requests <= 100: parser.error("模型请求上限需为1到100")
    if options.live and options.credentials_file is None: parser.error("实网模式必须提供指定凭据文件")
    mode = options.memory_mode or ('active' if options.adrian else 'legacy')
    from min_agent.web.launcher import DatabaseLease, DatabaseInUse, LaunchError, identity_for, open_existing, port_available, wait_ready
    identity = identity_for(options.database,options.port,demo=not (options.live or options.ollama),memory_mode=mode)
    lease = DatabaseLease(options.database,identity)
    try:
        lease.__enter__()
    except DatabaseInUse:
        if not options.adrian: parser.error("数据库已有网页服务，请先关闭原服务")
        try:
            url = open_existing(options.database,identity)
        except LaunchError as error:
            parser.error(str(error))
        print('Adrian已运行，已打开 '+url)
        return
    try:
        if not port_available(options.port):
            parser.error("本地端口已被其他程序占用；未启动或打开该程序")
        model = None
        if options.live:
            from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
            try:
                settings = DeepSeekSettings.from_credentials_file(options.credentials_file,
                    max_requests=options.max_requests,max_output_tokens=None)
                model = DeepSeekResponsesModel(settings,proxy=options.proxy)
            except (ValueError,OSError,ImportError):
                parser.error("真实模型配置无法加载，请核对项目外凭据文件和代理；没有回退Demo")
            from min_agent.model_context import context_window
            try:
                context_window(model)
            except ValueError:
                model.close()
                parser.error("无法核实当前模型上下文能力，请检查代理及模型元数据接口；未使用猜测窗口，也未回退Demo")
        elif options.ollama:
            from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model
            try:
                settings = OllamaSettings(model=options.ollama_model, base_url=options.ollama_url,
                    num_ctx=options.ollama_num_ctx, max_output_tokens=options.ollama_num_predict,
                    keep_alive=options.ollama_keep_alive, timeout_seconds=options.ollama_timeout,
                    max_requests=options.max_requests)
                # 启动前只读确认服务与模型；缺失时明确失败，不下载模型、不回退云端。
                require_model(settings)
                model = OllamaModel(settings)
            except ValueError as error:
                parser.error(_configuration_error(error))
            print(f"本地Ollama：{settings.base_url} 模型 {settings.model}（num_ctx={settings.num_ctx}，非流式）",flush=True)
            if settings.num_ctx < 8192:
                print("提示：num_ctx低于8192；完整系统提示与工具Schema实测约4094 tokens，可能立即触发context_limit。",flush=True)
        desktop = None
        if options.desktop:
            from min_agent.desktop_notifications import DesktopChannel
            desktop = DesktopChannel(port=options.port)
        harness = HarnessApplication(options.database,model=model,proxy=options.proxy,desktop=desktop,memory_mode=mode)
        run(harness,identity,open_browser=options.adrian and not options.no_browser)
    finally:
        lease.__exit__(None,None,None)


def run(harness,identity,*,open_browser=False):
    import uvicorn
    from min_agent.web.launcher import wait_ready, LaunchError
    app = create_app(harness,identity=identity)
    server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=identity['port'],access_log=False,log_level="warning"))
    app.state.shutdown_callback = lambda: setattr(server,'should_exit',True)
    stopped = Event()
    if open_browser:
        def ready():
            try: wait_ready(identity,stop_event=stopped)
            except LaunchError as error:
                if not stopped.is_set(): print(str(error),flush=True)
        Thread(target=ready,name='adrian-browser-ready',daemon=True).start()
    provider_name = getattr(harness.model,'_provider',None)
    settings = getattr(harness.model,'_settings',None)
    provider = ('离线Demo' if harness.demo else 'DeepSeek已配置' if provider_name=='deepseek.responses'
        else f"本地Ollama({getattr(settings,'model','model')})" if provider_name=='ollama' else '自定义Provider')
    print(f"Adrian {provider}工作台：http://127.0.0.1:{identity['port']}/",flush=True)
    print('关闭方式：网页“关闭服务”或在此窗口按Ctrl+C；关闭浏览器不会停止后台。',flush=True)
    try:
        server.run()
    finally:
        stopped.set()
        if not getattr(app.state,'harness_closed',False):harness.close()


if __name__ == "__main__":
    main()
