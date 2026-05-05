# 【開發用】：YouTubeBridge FastAPI hot reload launcher
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8091,
        reload=True,
        reload_dirs=["."],
        # FactCards are runtime data and are written during live tests; watching
        # Markdown would reload 8091 in the middle of automatic replenishment.
        reload_includes=["*.py", "*.html", "*.js", "*.css", "*.json"],
    )
