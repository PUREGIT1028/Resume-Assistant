import concurrent.futures
import time
from unittest.mock import Mock
import pytest
from app.persistence import Database, PersistentMap
from app.models.metadata import QueryMetadata


def test_persistence_survives_database_reopen(tmp_path):
    path=tmp_path/'state.sqlite3'
    records=PersistentMap(Database(path),'resumes')
    records['one']={'text':'sample','metadata':{'skills':['Python']}}
    reopened=PersistentMap(Database(path),'resumes')
    assert reopened['one']['metadata']['skills']==['Python']
    assert list(reopened)==['one']


def test_concurrent_submissions_create_one_job(tmp_path):
    db=Database(tmp_path/'state.sqlite3')
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:db.create_job('Python','same-key'),range(6)))
    assert sum(created for _,created in results)==1
    assert len({job['id'] for job,_ in results})==1
    db.update_job(results[0][0]['id'],status='completed',result={'candidates':[]})
    assert db.create_job('Python','same-key')[1] is False
    assert db.create_job('Python','changed-dataset')[1] is True


def test_restart_marks_unfinished_without_auto_retry(tmp_path):
    db=Database(tmp_path/'state.sqlite3')
    job,_=db.create_job('Python','same-key')
    db.update_job(job['id'],status='running')
    db.interrupt_unfinished()
    assert db.get_job(job['id'])['status']=='interrupted'
    assert len(db.history())==1


def test_completed_legacy_result_does_not_call_model_again(monkeypatch):
    from app.api import routes
    compute=Mock(return_value=Mock(model_dump=Mock(return_value={'candidates':[]})))
    monkeypatch.setattr(routes,'compute_screening',compute)
    monkeypatch.setattr(routes,'result_storage',{})
    assert routes.cached_screening('id')==routes.cached_screening('id')
    compute.assert_called_once()


def test_background_job_success_failure_and_cache(tmp_path,monkeypatch):
    from app import workbench
    from app.api import routes
    db=Database(tmp_path/'jobs.sqlite3')
    monkeypatch.setattr(workbench,'database',db)
    monkeypatch.setattr(routes,'resume_storage',{'resume':{'text':'Python'}})
    monkeypatch.setattr(routes,'query_storage',{})
    parser=Mock(return_value=QueryMetadata(required_skills=['Python']))
    monkeypatch.setattr(routes.query_parser,'parse_query',parser)
    pipeline=Mock(return_value={'query_id':'id','candidates':[]})
    monkeypatch.setattr(routes,'cached_screening',pipeline)
    service=workbench.JobService()
    try:
        first=service.submit('Python')
        deadline=time.monotonic()+3
        while db.get_job(first['id'])['status'] in ('queued','running') and time.monotonic()<deadline: time.sleep(.01)
        done=db.get_job(first['id'])
        assert done['status']=='completed'
        second=service.submit('Python')
        assert second['reused'] and second['id']==first['id']
        assert parser.call_count==1 and pipeline.call_count==1
        parser.side_effect=TimeoutError()
        bad=service.submit('Java')
        deadline=time.monotonic()+3
        while db.get_job(bad['id'])['status'] in ('queued','running') and time.monotonic()<deadline: time.sleep(.01)
        assert db.get_job(bad['id'])['status']=='failed'
        assert '超时' in db.get_job(bad['id'])['error']
    finally: service.executor.shutdown(wait=True)


def test_usage_stores_counts_and_task_attribution(tmp_path,monkeypatch):
    from app import usage
    monkeypatch.setattr(usage,'database',Database(tmp_path/'usage.sqlite3'))
    token=usage.task_context.set('job')
    try:
        usage.record_usage('model','llm',{'input_tokens':1000,'output_tokens':100})
        usage.record_usage('model','llm')
    finally: usage.task_context.reset(token)
    report=usage.usage_summary('job')
    assert report['calls']==2 and report['unreported_calls']==1
    assert report['input_tokens']==1000 and report['output_tokens']==100
    assert report['estimated_cost_cny']==pytest.approx(.0008)


def test_duplicate_upload_reuses_id_without_llm(monkeypatch):
    import hashlib
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    content=b'Python resume'
    monkeypatch.setattr(routes,'resume_storage',{'same':{'content_hash':hashlib.sha256(content).hexdigest()}})
    extractor=Mock()
    monkeypatch.setattr(routes.metadata_extractor,'extract_metadata',extractor)
    response=TestClient(app).post('/api/v1/resumes',files={'file':('resume.txt',content,'text/plain')})
    assert response.status_code==200 and response.json()['resume_id']=='same'
    extractor.assert_not_called()

def test_task_http_accepts_and_reuses_queued_job(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import workbench
    from app.api import routes
    db=Database(tmp_path/'http.sqlite3')
    monkeypatch.setattr(workbench,'database',db)
    monkeypatch.setattr(routes,'resume_storage',{'r':{'text':'Python'}})
    service=workbench.JobService()
    # Keep the task queued so the HTTP response can be verified without a model.
    service.executor.shutdown(wait=True)
    service.executor=Mock()
    monkeypatch.setattr(workbench,'service',service)
    client=TestClient(app)
    first=client.post('/api/v1/tasks',json={'query_text':'Python'})
    assert first.status_code==202 and first.json()['status']=='queued'
    second=client.post('/api/v1/tasks',json={'query_text':'Python'})
    assert second.json()['reused'] and second.json()['id']==first.json()['id']
    service.executor.submit.assert_called_once()
    assert client.get('/api/v1/history').json()['total']==1
    assert client.get('/api/v1/tasks/'+first.json()['id']).status_code==200
    assert client.post('/api/v1/tasks/'+first.json()['id']+'/retry').status_code==409


def test_changed_library_does_not_reuse_result(tmp_path,monkeypatch):
    from app import workbench
    from app.api import routes
    monkeypatch.setattr(routes,'resume_storage',{'a':{'text':'Python'}})
    original=workbench.fingerprint('Python')
    routes.resume_storage['b']={'text':'Java'}
    assert workbench.fingerprint('Python')!=original

def test_delete_and_restore_keep_snapshot_without_model(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    db=Database(tmp_path/'delete.sqlite3')
    resumes=PersistentMap(db,'resumes'); trash=PersistentMap(db,'trash')
    resumes['r']={'id':'r','filename':'example.txt','text':'Python','metadata':{'name':'Example'}}
    snapshot={'id':'r','vector':[.1,.2],'document':'Python','metadata':{'name':'Example'}}
    store=Mock();store.export_document.return_value=snapshot
    monkeypatch.setattr(routes,'database',db)
    monkeypatch.setattr(routes,'resume_storage',resumes)
    monkeypatch.setattr(routes,'trash_storage',trash)
    monkeypatch.setattr(routes,'vector_store_manager',store)
    extractor=Mock();monkeypatch.setattr(routes.metadata_extractor,'extract_metadata',extractor)
    client=TestClient(app)
    assert client.delete('/api/v1/resumes/r').status_code==200
    assert 'r' not in resumes and trash['r']['vector']==snapshot
    assert client.get('/api/v1/trash').json()['resumes'][0]['resume_id']=='r'
    assert client.post('/api/v1/trash/r/restore').status_code==200
    assert 'r' in resumes and 'r' not in trash
    store.restore_document.assert_called_once_with('resumes',snapshot)
    extractor.assert_not_called()


def test_failed_vector_backup_does_not_delete_record(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    db=Database(tmp_path/'delete-fail.sqlite3')
    records=PersistentMap(db,'resumes');records['r']={'text':'sample'}
    store=Mock();store.export_document.side_effect=RuntimeError()
    monkeypatch.setattr(routes,'database',db)
    monkeypatch.setattr(routes,'resume_storage',records)
    monkeypatch.setattr(routes,'vector_store_manager',store)
    assert TestClient(app).delete('/api/v1/resumes/r').status_code==503
    assert 'r' in records


def test_index_cleanup_failure_is_marked_and_record_is_archived(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    db=Database(tmp_path/'delete-pending.sqlite3')
    records=PersistentMap(db,'resumes');records['r']={'text':'sample'}
    trash=PersistentMap(db,'trash')
    store=Mock();store.export_document.return_value=None;store.delete_documents.side_effect=RuntimeError()
    monkeypatch.setattr(routes,'database',db)
    monkeypatch.setattr(routes,'resume_storage',records)
    monkeypatch.setattr(routes,'trash_storage',trash)
    monkeypatch.setattr(routes,'vector_store_manager',store)
    response=TestClient(app).delete('/api/v1/resumes/r')
    assert response.status_code==200 and response.json()['cleanup_pending']
    assert 'r' not in records and 'r' in trash

def test_chroma_vector_delete_restore_roundtrip_never_reembeds(tmp_path,monkeypatch):
    from app.core.vector_store import VectorStoreManager
    store=VectorStoreManager(persist_directory=str(tmp_path/'vectors'))
    store.add_documents('resumes',['sample'],[{'name':'Example'}],['id'])
    saved=store.export_document('resumes','id')
    assert saved['document']=='sample' and saved['vector']
    monkeypatch.setattr(store.embeddings,'embed_documents',Mock(side_effect=AssertionError('must not embed')))
    store.delete_documents('resumes',['id'])
    assert store.export_document('resumes','id') is None
    store.restore_document('resumes',saved)
    assert store.export_document('resumes','id')['metadata']['name']=='Example'

def test_original_file_preview_download_and_missing_file_fallback(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    monkeypatch.setattr(routes,'FILES_DIR',tmp_path)
    content=b'%PDF-1.4\noriginal resume bytes'
    (tmp_path/'r.pdf').write_bytes(content)
    records={'r':{'id':'r','filename':'example.pdf','text':'extracted text','original_file':'r.pdf'},
             'old':{'id':'old','filename':'old.pdf','text':'old extracted content'}}
    monkeypatch.setattr(routes,'resume_storage',records)
    client=TestClient(app)
    preview=client.get('/api/v1/resumes/r/preview').json()
    assert preview['has_original'] and preview['is_pdf']
    response=client.get('/api/v1/resumes/r/file?download=true')
    assert response.content==content and response.headers['content-disposition'].startswith('attachment')
    assert client.get('/api/v1/resumes/r/file').headers['content-disposition'].startswith('inline')
    assert client.get('/api/v1/resumes/old/preview').json()['has_original'] is False
    fallback=client.get('/api/v1/resumes/old/file?download=true')
    assert fallback.text=='old extracted content' and 'text/plain' in fallback.headers['content-type']
    assert client.get('/api/v1/resumes/missing/preview').status_code==404
    (tmp_path/'r.pdf').unlink()
    assert client.get('/api/v1/resumes/r/file?download=true').text=='extracted text'


def test_original_path_cannot_escape_upload_directory(tmp_path,monkeypatch):
    from app.api import routes
    root=tmp_path/'uploads';root.mkdir()
    (tmp_path/'private.txt').write_text('private')
    monkeypatch.setattr(routes,'FILES_DIR',root)
    assert routes.original_path({'original_file':'../private.txt'}) is None
    with pytest.raises(ValueError):
        routes.preserve_original({'id':'../escape','filename':'sample.txt'},b'test')


def test_new_upload_saves_exact_original_bytes(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes
    from app.models.metadata import ResumeMetadata
    records={}
    monkeypatch.setattr(routes,'FILES_DIR',tmp_path)
    monkeypatch.setattr(routes,'resume_storage',records)
    monkeypatch.setattr(routes.metadata_extractor,'extract_metadata',Mock(return_value=ResumeMetadata(name='Example')))
    monkeypatch.setattr(routes.retriever,'add_resume',Mock())
    client=TestClient(app)
    content='Python resume\nOriginal lines'.encode()
    upload=client.post('/api/v1/resumes',files={'file':('sample.txt',content,'text/plain')})
    assert upload.status_code==200
    rid=upload.json()['resume_id']
    assert routes.original_path(records[rid]).read_bytes()==content
    assert client.get(f'/api/v1/resumes/{rid}/file?download=true').content==content
