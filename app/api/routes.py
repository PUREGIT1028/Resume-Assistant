"""
API 路由
"""
import asyncio
import uuid
import json
import hashlib
import os
from pathlib import Path
from urllib.parse import quote
import threading
from app.persistence import database, PersistentMap
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse, Response
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.models import (
    UploadResumeResponse, QueryRequest, QueryResponse, ScreeningResult
)
from app.core.cache_manager import CacheManager
from app.core.document_parser import DocumentParser
from app.core.extractor import MetadataExtractor
from app.core.llm_client import LLMClient
from app.core.query_parser import QueryParser
from app.core.vector_store_factory import get_vector_store_manager
from app.core.retriever import Retriever
from app.core.filter import HardFilter
from app.core.scorer import Scorer
from app.core.ranker import Ranker
from app.core.analyzer import CandidateAnalyzer
from app.core.result_formatter import ResultFormatter
from app.models.metadata import ResumeMetadata, QueryMetadata

router = APIRouter(prefix="/api/v1")

# 常量
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
FILES_DIR = Path(os.getenv('RESUME_FILES_DIR', './data/files')).resolve()
FILES_DIR.mkdir(parents=True, exist_ok=True)


def original_path(record):
    name=record.get('original_file')
    if not name: return None
    path=(FILES_DIR / name).resolve()
    if path.parent != FILES_DIR or not path.is_file(): return None
    return path


def preserve_original(record, content):
    suffix=Path(record['filename']).suffix.lower()
    name=f"{record['id']}{suffix}"
    path=(FILES_DIR/name).resolve()
    if path.parent != FILES_DIR: raise ValueError('Invalid stored file identifier')
    path.write_bytes(content)
    record['original_file']=name
    record['file_size']=len(content)
    return path


# 初始化核心组件
llm_client = LLMClient()
cache_manager = CacheManager()
document_parser = DocumentParser(cache_manager=cache_manager)
metadata_extractor = MetadataExtractor(llm_client, cache_manager=cache_manager)
query_parser = QueryParser(llm_client)
vector_store_manager = get_vector_store_manager()
retriever = Retriever(vector_store_manager)
hard_filter = HardFilter()
scorer = Scorer()
ranker = Ranker()
candidate_analyzer = CandidateAnalyzer(llm_client)
result_formatter = ResultFormatter()

# 存储简历和查询结果的内存字典（在实际应用中应使用数据库）
resume_storage = PersistentMap(database, 'resumes')
query_storage = PersistentMap(database, 'queries')
result_storage = PersistentMap(database, 'results')
trash_storage = PersistentMap(database, 'trash')
_result_lock = threading.Lock()
_library_write_lock = threading.Lock()


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    """安全解析 JSON 字符串；若已是目标类型则直接返回。"""
    if default is None:
        default = []
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"JSON parse failed for: {value}")
        return default


def _calc_skill_scores(resume_skills: list, query_metadata: QueryMetadata, overall_skill_score: float) -> list:
    """根据查询要求计算每个技能的单项得分。"""
    if not resume_skills:
        return []

    required = [s.lower() for s in query_metadata.required_skills]
    preferred = [s.lower() for s in query_metadata.preferred_skills]

    scores = []
    for skill in resume_skills:
        sl = str(skill).lower()
        matched = False
        for q in required + preferred:
            if q in sl or sl in q:
                matched = True
                break
        scores.append({
            "name": skill,
            "score": 1.0 if matched else max(overall_skill_score - 0.3, 0.0)
        })
    return scores


@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok"}


@router.get("/resumes")
async def list_resumes():
    """列出已上传的简历（摘要信息）。"""
    items = []
    for rid, data in resume_storage.items():
        meta = data.get("metadata", {}) or {}
        items.append({
            "resume_id": rid,
            "filename": data.get("filename", ""),
            "name": meta.get("name", ""),
            "created_at": data.get("created_at"),
            "has_original": original_path(data) is not None,
        })
    return {"total": len(items), "resumes": items}


@router.post("/resumes", response_model=UploadResumeResponse)
async def upload_resume(file: UploadFile = File(...)):
    """
    上传简历接口
    """
    logger.info(f"[upload_resume] 开始处理文件: {file.filename}")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    content = await file.read(MAX_FILE_SIZE + 1)
    if not file.filename.lower().endswith(('.pdf','.txt','.md')):
        raise HTTPException(status_code=400, detail='仅支持 PDF、TXT、MD 文件')
    if not content:
        raise HTTPException(status_code=400, detail='文件内容不能为空')
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"文件大小超过限制 {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")

    content_hash = hashlib.sha256(content).hexdigest()
    for existing_id, existing in resume_storage.items():
        if existing.get('content_hash') == content_hash:
            if original_path(existing) is None:
                existing.setdefault('id',existing_id)
                existing.setdefault('filename',file.filename)
                preserve_original(existing,content)
                resume_storage[existing_id]=existing
            return UploadResumeResponse(resume_id=existing_id, message='相同文件已在资料库中，已复用解析结果，无需重复调用模型。')
    resume_id = str(uuid.uuid4())
    logger.info(f"[upload_resume] 文件读取完成, 大小: {len(content)} bytes, resume_id: {resume_id}")

    try:
        if file.filename.lower().endswith('.pdf'):
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            logger.info(f"[upload_resume] 临时PDF文件已保存: {tmp_path}")
            try:
                resume_text = await run_in_threadpool(document_parser.parse_pdf, tmp_path)
                logger.info(f"[upload_resume] PDF解析完成, 文本长度: {len(resume_text)}")
            finally:
                os.remove(tmp_path)
                logger.info(f"[upload_resume] 临时PDF文件已删除: {tmp_path}")
        else:
            try:
                resume_text = content.decode('utf-8')
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="非PDF文件必须是UTF-8编码的文本")
            logger.info(f"[upload_resume] 非PDF文件解析完成, 文本长度: {len(resume_text)}")

        if not resume_text.strip():
            raise HTTPException(status_code=422, detail='没有提取到文字，请使用文字版 PDF；扫描件暂不支持。')
        metadata = await run_in_threadpool(metadata_extractor.extract_metadata, resume_text)
        logger.info("Resume metadata extracted")

        record = {
            "id": resume_id,
            "filename": file.filename,
            "content_hash": content_hash,
            "text": resume_text,
            "metadata": metadata.dict(),
            "created_at": datetime.now()
        }
        logger.info(f"[upload_resume] 简历已存储, resume_id: {resume_id}")

        await run_in_threadpool(retriever.add_resume, resume_id, resume_text, metadata.dict())
        saved_path=preserve_original(record,content)
        try: resume_storage[resume_id] = record
        except Exception:
            saved_path.unlink(missing_ok=True)
            raise
        logger.info("Resume indexed and persisted")

        return UploadResumeResponse(
            resume_id=resume_id,
            message=f"简历 '{file.filename}' 上传成功"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("上传简历失败")
        raise HTTPException(status_code=500, detail="上传简历失败，请稍后重试")


@router.post("/queries", response_model=QueryResponse)
async def submit_query(query_request: QueryRequest):
    """
    提交筛选查询接口
    """
    try:
        query_metadata = await run_in_threadpool(query_parser.parse_query, query_request.query_text)
        query_id = str(uuid.uuid4())

        query_storage[query_id] = {
            "id": query_id,
            "text": query_request.query_text,
            "metadata": query_metadata.dict(),
            "created_at": datetime.now()
        }

        return QueryResponse(
            query_id=query_id,
            message="查询提交成功"
        )

    except Exception as e:
        logger.exception("提交查询失败")
        raise HTTPException(status_code=500, detail="提交查询失败，请稍后重试")


def compute_screening(query_id, progress=lambda stage, percent: None):
    if query_id not in query_storage:
        raise HTTPException(status_code=404, detail='查询不存在')
    try:
        query_data = query_storage[query_id]
        query_metadata = QueryMetadata(**query_data["metadata"])

        progress('检索相关简历',35)
        retrieved_resumes = retriever.retrieve(query_metadata)
        progress('检查岗位条件',50)
        retrieved_resumes = [r for r in retrieved_resumes if r.get("id") in resume_storage]
        from app.core.relaxed_matching import relaxed_candidates
        filtered_resumes = relaxed_candidates(retrieved_resumes, resume_storage, query_metadata, hard_filter)
        progress('计算匹配分数',60)
        scored_resumes = scorer.score_resumes(filtered_resumes, query_metadata)
        ranked_resumes = ranker.rank_resumes(scored_resumes, query_metadata)
        progress('生成候选人分析报告',75)
        analyzed_candidates = candidate_analyzer.analyze_candidates(ranked_resumes, query_metadata)
        progress('整理匹配结果',95)
        formatted_results = result_formatter.format_results(analyzed_candidates, query_metadata)

        candidates = []
        for candidate_data in formatted_results["candidates"]:
            basic_info = candidate_data.get("basic_info", {}) or {}
            scores = candidate_data.get("scores", {}) or {}
            overall_skill_score = scores.get("skill_score", 0)

            resume_skills = _safe_json_loads(basic_info.get("skills", []), [])
            skill_scores = _calc_skill_scores(resume_skills, query_metadata, overall_skill_score)

            work_experience = _safe_json_loads(basic_info.get("work_experience", []), [])
            education = _safe_json_loads(basic_info.get("education", []), [])

            candidate = {
                "id": candidate_data.get("id", ""),
                "rank": candidate_data.get("rank", 0),
                "name": candidate_data.get("name", ""),
                "email": candidate_data.get("contact_info", {}).get("email"),
                "phone": candidate_data.get("contact_info", {}).get("phone"),
                "overall_score": scores.get("overall_score", 0),
                "work_experience": work_experience,
                "education": education,
                "skill_scores": skill_scores,
                "skills": resume_skills,
                "expected_salary": basic_info.get("expected_salary"),
                "preferred_locations": basic_info.get("preferred_locations", []),
                "match_notes": candidate_data.get("match_notes", []),
                "analysis": candidate_data.get("analysis", "")
            }
            candidates.append(candidate)

        return ScreeningResult(
            query_id=query_id,
            query_text=query_data["text"],
            total_candidates=formatted_results["total_candidates"],
            screening_note="宽松匹配：保留部分符合条件的候选人，按规则分数排序；未确认条件见各人提示。",
            candidates=candidates,
            created_at=query_data["created_at"]
        )

    except HTTPException:
        raise
    except Exception:
        logger.warning('Screening computation failed')
        raise


def cached_screening(query_id, progress=lambda stage, percent: None):
    # Serialize legacy result requests too: a repeated GET must not bill again.
    with _result_lock:
        if query_id in result_storage:
            return result_storage[query_id]
        result = compute_screening(query_id, progress)
        payload = result.model_dump(mode='json')
        result_storage[query_id] = payload
        return payload


@router.get('/results/{query_id}', response_model=ScreeningResult)
async def get_screening_results(query_id: str):
    if query_id not in query_storage:
        raise HTTPException(status_code=404, detail='查询不存在')
    try:
        return await run_in_threadpool(cached_screening, query_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail='模型处理失败，请稍后重试或检查账号额度与网络。')


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: str):
    """
    获取简历详情接口
    """
    if resume_id not in resume_storage:
        raise HTTPException(status_code=404, detail="简历不存在")

    try:
        return resume_storage[resume_id]
    except Exception:
        logger.exception("获取简历详情失败")
        raise HTTPException(status_code=500, detail="获取简历详情失败，请稍后重试")


@router.get('/trash')
def list_trash():
    return {'resumes':[{'resume_id':rid,'filename':archive['record'].get('filename',''),
        'name':archive['record'].get('metadata',{}).get('name',''),'deleted_at':archive['deleted_at'],
        'cleanup_pending':archive.get('cleanup_pending',False)} for rid,archive in trash_storage.items()]}


def _archive_resume_sync(resume_id):
    if resume_id not in resume_storage: raise HTTPException(404,'简历不存在')
    # Save the existing embedding so recovery never needs a new paid model call.
    try: snapshot=vector_store_manager.export_document('resumes',resume_id)
    except Exception: raise HTTPException(503,'暂时无法备份索引，未删除简历，请稍后重试。')
    try: archive=database.archive_resume(resume_id,snapshot)
    except KeyError: raise HTTPException(404,'简历不存在')
    pending=False
    try: vector_store_manager.delete_documents('resumes',[resume_id])
    except Exception: pending=True
    archive['cleanup_pending']=pending
    trash_storage[resume_id]=archive
    return {'resume_id':resume_id,'cleanup_pending':pending,
        'message':'已移入本地回收站，历史筛选仍保留。'+('检索索引清理暂未完成；已从新筛选中排除。' if pending else '')}


@router.delete('/resumes/{resume_id}')
async def delete_resume(resume_id:str):
    def archive():
        with _library_write_lock:
            return _archive_resume_sync(resume_id)
    return await run_in_threadpool(archive)


@router.post('/trash/{resume_id}/restore')
async def restore_resume(resume_id:str):
    def restore():
        if resume_id not in trash_storage: raise HTTPException(404,'回收站中没有这份简历')
        archive=trash_storage[resume_id]
        if archive.get('vector'):
            try: vector_store_manager.restore_document('resumes',archive['vector'])
            except Exception: raise HTTPException(503,'暂时无法恢复检索索引，备份仍保留，请稍后重试。')
        database.restore_resume(resume_id)
        return {'resume_id':resume_id,'message':'简历已恢复，没有重新调用模型。'}
    def locked_restore():
        with _library_write_lock:
            return restore()
    return await run_in_threadpool(locked_restore)


@router.get('/resumes/{resume_id}/preview')
def preview_resume(resume_id:str):
    if resume_id not in resume_storage: raise HTTPException(404,'简历不存在，可能已移入回收站')
    record=resume_storage[resume_id]
    path=original_path(record)
    return {'resume_id':resume_id,'filename':record.get('filename','简历'),'text':record.get('text',''),
        'has_original':path is not None,'is_pdf':path is not None and path.suffix.lower()=='.pdf',
        'note':'已保存原始文件，可直接下载。' if path else '此旧记录未保留原件，可下载 PDF 文字重建版，排版与原件不同。'}


@router.get('/resumes/{resume_id}/file')
def resume_file(resume_id:str,download:bool=False):
    if resume_id not in resume_storage: raise HTTPException(404,'简历不存在，可能已移入回收站')
    record=resume_storage[resume_id]
    path=original_path(record)
    headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'}
    if path:
        return FileResponse(path,filename=record.get('filename','resume'+path.suffix),
            media_type='application/pdf' if path.suffix.lower()=='.pdf' else 'text/plain; charset=utf-8',
            content_disposition_type='attachment' if download else 'inline',headers=headers)
    name=Path(record.get('filename','简历')).stem+'-提取文字.txt'
    headers['Content-Disposition']=("attachment" if download else "inline")+"; filename*=UTF-8''"+quote(name)
    return Response(record.get('text',''),media_type='text/plain; charset=utf-8',headers=headers)


@router.get('/resumes/{resume_id}/pdf')
def resume_pdf(resume_id: str, download: bool = True):
    if resume_id not in resume_storage:
        raise HTTPException(404,'简历不存在，可能已移入回收站')
    record=resume_storage[resume_id]
    path=original_path(record)
    headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'}
    disposition='attachment' if download else 'inline'
    if path and path.suffix.lower()=='.pdf':
        return FileResponse(path,filename=record.get('filename','resume.pdf'),media_type='application/pdf',content_disposition_type=disposition,headers=headers)
    from app.core.pdf_export import build_resume_pdf
    name=Path(record.get('filename','简历')).stem+'-文字重建版.pdf'
    headers['Content-Disposition']=disposition+"; filename*=UTF-8''"+quote(name)
    return Response(build_resume_pdf(record),media_type='application/pdf',headers=headers)


@router.delete('/trash/{resume_id}')
async def permanently_delete_resume(resume_id: str):
    def remove():
        with _library_write_lock:
            if resume_id not in trash_storage:
                raise HTTPException(404, '回收站中没有这份简历')
            archive=trash_storage[resume_id]
            try:
                vector_store_manager.delete_documents('resumes', [resume_id])
            except Exception:
                raise HTTPException(503, '暂时无法清理检索索引，回收站记录仍保留，请稍后重试。')
            path=original_path(archive['record'])
            if path:
                # Never remove an original still referenced by another record.
                shared=any(original_path(record)==path for record in resume_storage.values()) or any(
                    rid!=resume_id and original_path(other['record'])==path for rid,other in trash_storage.items())
                if not shared:
                    try: path.unlink(missing_ok=True)
                    except OSError:
                        raise HTTPException(503, '暂时无法删除原文件，回收站记录仍保留，请稍后重试。')
            del trash_storage[resume_id]
            return {'resume_id':resume_id, 'message':'简历已永久删除，无法从回收站恢复。已有历史筛选报告仍保留。'}
    return await run_in_threadpool(remove)
