# 【開發用】：MemoriaCore FastAPI hot reload launcher
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8088,
        reload=True,
        reload_dirs=["."],
        reload_includes=["*.py", "*.json", "*.html", "*.js", "*.css"],
    )
