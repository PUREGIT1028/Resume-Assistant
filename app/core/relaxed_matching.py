"""Retain partial matches for human review instead of silently dropping them."""
from app.models.metadata import QueryMetadata

def relaxed_candidates(retrieved, records, query, hard_filter):
    # Small libraries should be compared as a whole, including stale-index omissions.
    ids=list(dict.fromkeys(r.get('id') for r in retrieved if r.get('id') in records))
    if len(records)<=10 or not ids:
        ids=list(dict.fromkeys(ids+list(records)))[:10]
    checks=(('required_skills','技能'),('min_experience_years','经验年限'),
            ('required_education','学历'),('locations','地点'),
            ('required_languages','语言'),('required_certifications','证书'))
    candidates=[]
    for rid in ids:
        record=records[rid]
        candidate={'id':rid,'text':record.get('text',''),'metadata':record.get('metadata',{})}
        notes=[]
        for field,label in checks:
            value=getattr(query,field)
            if value is None or value==[] or value=='': continue
            check=QueryMetadata(**{field:value})
            if not hard_filter.filter_resumes([candidate],check):
                notes.append(label+'条件未完全确认（可能不符或简历信息不足），建议人工核实')
        candidate['match_notes']=notes
        candidates.append(candidate)
    return candidates
