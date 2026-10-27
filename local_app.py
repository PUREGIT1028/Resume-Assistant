"""Local deployment entry point."""
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name('.env'), override=True, encoding='utf-8-sig')
import sys
from loguru import logger
logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"), backtrace=False, diagnose=False)
from fastapi.responses import JSONResponse
from app.main import app

def configured():
    return all(os.environ.get(n, '').strip() not in ('', 'NOT_CONFIGURED') for n in ('LLM_API_KEY', 'EMBEDDING_API_KEY'))

@app.get('/api/v1/deployment-status')
def deployment_status():
    from config.config import settings
    return {'configured': configured(), 'provider': 'Zhipu', 'revision': 'workbench-v3', 'llm_model': settings.LLM_MODEL, 'embedding_model': settings.EMBEDDING_MODEL}

@app.middleware('http')
async def require_model_configuration(request, call_next):
    needs_model = (request.method == 'POST' and (request.url.path in ('/api/v1/resumes', '/api/v1/queries', '/api/v1/tasks') or request.url.path.endswith('/retry')))
    if needs_model and not configured():
        return JSONResponse(status_code=503, content={'detail': '尚未配置智谱 API Key。请运行项目目录中的 Configure.cmd，填写密钥后自动重启服务。'})
    return await call_next(request)
