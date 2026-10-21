"""以完整参数启动本地后台；默认离线，不隐式读取凭据或代理。"""

import argparse
from pathlib import Path

from min_agent.application import HarnessApplication
from min_agent.web.app import create_app


def main():
    parser = argparse.ArgumentParser(description="本地Agent Harness工作台")
    parser.add_argument("--database", default="data/workspace.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--live", action="store_true", help="允许将用户请求发送至指定DeepSeek官方API")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--proxy", help="模型/搜索的显式本机HTTP代理；固定DNS网页读取直连，不读取环境")
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--desktop", action="store_true", help="启用Windows桌面通知；API接受不等于实际显示")
    parser.add_argument("--demo", action="store_true", help="显式离线演示，与--live互斥")
    options = parser.parse_args()
    if options.live and options.demo: parser.error("--live与--demo不能同时使用")
    if not 1024 <= options.port <= 65535: parser.error("端口需为1024到65535")
    model = None
    if options.live:
        if options.credentials_file is None: parser.error("实网模式必须提供指定凭据文件")
        from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
        model = DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(options.credentials_file,
            max_requests=options.max_requests, max_output_tokens=4096), proxy=options.proxy)
    desktop = None
    if options.desktop:
        from min_agent.desktop_notifications import DesktopChannel
        desktop = DesktopChannel(port=options.port)
    harness = HarnessApplication(options.database, model=model, proxy=options.proxy, desktop=desktop)
    import uvicorn
    uvicorn.run(create_app(harness), host="127.0.0.1", port=options.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
