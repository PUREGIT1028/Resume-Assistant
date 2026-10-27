"""Usage accounting: store counts only, never prompts, keys or resume content."""
from contextvars import ContextVar
import os
import uuid
from app.persistence import database, now

task_context=ContextVar('usage_task',default=None)


def record_usage(model, kind, usage=None):
    usage=usage if isinstance(usage,dict) else {}
    reported=bool(usage) and any(k in usage for k in ('input_tokens','prompt_tokens','total_tokens'))
    input_count=int(usage.get('input_tokens',usage.get('prompt_tokens',usage.get('total_tokens',0))) or 0)
    output_count=int(usage.get('output_tokens',usage.get('completion_tokens',0)) or 0)
    try:
        input_rate=float(os.getenv('EMBEDDING_PRICE_PER_MILLION','0.5') if kind=='embedding' else os.getenv('LLM_INPUT_PRICE_PER_MILLION','0.5'))
        output_rate=float(os.getenv('LLM_OUTPUT_PRICE_PER_MILLION','3')) if kind!='embedding' else 0
    except ValueError:
        input_rate=output_rate=0
    cost=(input_count*input_rate+output_count*output_rate)/1000000 if reported else 0
    with database.connect() as db:
        db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,task_context.get(),model,kind,input_count,output_count,int(reported),cost,now()))


def embedding_response(response):
    # Embedding responses are non-streaming JSON. Keep only aggregate usage.
    if not response.request.url.path.rstrip('/').endswith('/embeddings'): return
    usage=None
    model=os.getenv('EMBEDDING_MODEL','embedding-3')
    if response.is_success:
        try:
            response.read()
            payload=response.json(); usage=payload.get('usage'); model=payload.get('model',model)
        except Exception:
            pass
    record_usage(model,'embedding',usage)


def usage_summary(task_id=None):
    sql='SELECT count(*) calls, coalesce(sum(input_tokens),0) input_tokens, coalesce(sum(output_tokens),0) output_tokens, coalesce(sum(cost),0) estimated_cost_cny, coalesce(sum(CASE WHEN reported=0 THEN 1 ELSE 0 END),0) unreported_calls FROM usage'
    with database.connect() as db:
        row=db.execute(sql+(' WHERE task_id=?' if task_id else ''),(task_id,) if task_id else ()).fetchone()
    result=dict(row); result['estimated_cost_cny']=round(result['estimated_cost_cny'],6)
    result['note']='自本次升级起统计服务商返回的用量，不含升级前的历史消耗；价格可在 .env 调整；不包含赠送额度、缓存折扣和未返回用量的调用，以平台账单为准。'
    return result
