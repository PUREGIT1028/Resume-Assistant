"""Durable background screening jobs for the local, single-process server."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.api import routes
from app.persistence import database, now
from app.usage import task_context, usage_summary
from app.models.metadata import QueryMetadata

router=APIRouter(prefix='/api/v1')

class TaskRequest(BaseModel):
    query_text: str = Field(min_length=1,max_length=8000)


def fingerprint(text):
    records=[(key,value.get('content_hash') or hashlib.sha256(value.get('text','').encode()).hexdigest()) for key,value in routes.resume_storage.items()]
    config=[os.getenv(name,'') for name in ('LLM_MODEL','LLM_BASE_URL','EMBEDDING_MODEL','EMBEDDING_BASE_URL')]
    return hashlib.sha256(json.dumps(['workbench-v3-relaxed-report',text,sorted(records),config],ensure_ascii=False).encode()).hexdigest()


def friendly_error(exc):
    if isinstance(exc,RuntimeError) and str(exc)=='dataset_changed': return '处理期间简历库发生变化，请重新提交，以免复用过期的匹配结果。'
    name=type(exc).__name__.lower()
    if 'timeout' in name: return '模型请求超时（单次最多等待 30 秒）。请稍后重试，已保存任务记录。'
    if 'authentication' in name or 'permission' in name: return '模型接口鉴权失败，请检查本地密钥和模型权限。'
    if 'ratelimit' in name: return '模型服务限制了请求，请检查并发限制和账号额度后再试。'
    if 'connection' in name: return '无法连接模型服务，请检查网络后重试。'
    if isinstance(exc,ValueError): return '模型没有返回可用的结构化内容，请稍后重试。'
    return '筛选未完成，记录已保存。请检查模型服务、网络和账号额度后重试。'


class JobService:
    def __init__(self):
        database.interrupt_unfinished()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='screening')

    def submit(self,text):
        text=' '.join(text.split())
        if not text: raise HTTPException(422,'请输入岗位需求')
        if not len(routes.resume_storage): raise HTTPException(409,'请先上传至少一份简历')
        try:
            job,created=database.create_job(text,fingerprint(text))
        except ValueError as exc:
            raise HTTPException(429,str(exc))
        if created:
            try: self.executor.submit(self.run,job['id'])
            except Exception:
                database.update_job(job['id'],status='failed',stage='任务启动失败',error='请稍后重试。')
                raise HTTPException(503,'暂时无法启动任务')
        return public_job(database.get_job(job['id']),reused=not created)

    def run(self,job_id):
        token=task_context.set(job_id)
        started=time.monotonic()
        def progress(stage,percent):
            database.update_job(job_id,status='running',stage=stage,progress=percent,elapsed_seconds=round(time.monotonic()-started,1))
        try:
            job=database.get_job(job_id)
            if job.get('dataset_key') != fingerprint(job['text']):
                raise RuntimeError('dataset_changed')
            progress('理解岗位需求',10)
            metadata=routes.query_parser.parse_query(job['text'])
            routes.query_storage[job_id]={'id':job_id,'text':job['text'],'metadata':metadata.model_dump(),'created_at':job['created_at']}
            # cached_screening preserves the result even if the server stops
            # between completion and the final task-status update.
            result=routes.cached_screening(job_id,progress)
            if job.get('dataset_key') != fingerprint(job['text']):
                raise RuntimeError('dataset_changed')
            database.update_job(job_id,status='completed',stage='筛选完成',progress=100,result=result,elapsed_seconds=round(time.monotonic()-started,1),finished_at=now())
        except Exception as exc:
            database.update_job(job_id,status='failed',stage='处理失败',error=friendly_error(exc),elapsed_seconds=round(time.monotonic()-started,1),finished_at=now())
        finally:
            task_context.reset(token)


def public_job(job,reused=False,include_result=True):
    result=dict(job)
    result['reused']=reused
    result['usage']=usage_summary(job['id'])
    if not include_result: result.pop('result',None)
    if job['status']=='running':
        try: result['elapsed_seconds']=round((datetime.fromisoformat(now())-datetime.fromisoformat(job['created_at'])).total_seconds(),1)
        except ValueError: pass
    return result

service=JobService()

@router.post('/tasks',status_code=202)
def create_task(request:TaskRequest):
    return service.submit(request.query_text)

@router.get('/tasks/{job_id}')
def get_task(job_id:str):
    job=database.get_job(job_id)
    if not job: raise HTTPException(404,'任务不存在')
    return public_job(job)

@router.post('/tasks/{job_id}/retry',status_code=202)
def retry_task(job_id:str):
    job=database.get_job(job_id)
    if not job: raise HTTPException(404,'任务不存在')
    if job['status'] not in ('failed','interrupted'): raise HTTPException(409,'仅失败或中断的任务需要重试')
    return service.submit(job['text'])

@router.get('/history')
def history():
    items=database.history()
    return {'tasks':[public_job(job,include_result=False) for job in items],'total':len(items)}

@router.get('/usage/summary')
def usage():
    return usage_summary()
