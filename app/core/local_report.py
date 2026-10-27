"""Evidence-based report when the remote analysis is unavailable."""
from app.core.metadata_utils import coerce_to_list

def local_report(resume, query):
    meta=resume.get('metadata',{}) or {}
    skills=[str(s) for s in coerce_to_list(meta.get('skills',[]))]
    evidence=str(resume.get('text','')).lower()
    def matched(skill):
        key=skill.lower()
        return any(key in s.lower() or s.lower() in key for s in skills) or key in evidence
    required=query.required_skills or []
    hits=[s for s in required if matched(s)]
    unknown=[s for s in required if not matched(s)]
    preferred=[s for s in (query.preferred_skills or []) if matched(s)]
    score=resume.get('scores',{}).get('overall_score')
    lines=['> 本地规则分析：AI 详细报告请求未成功。以下内容依据已提取简历与规则评分整理，不是大模型生成的评价。',
           '## 1. 综合结论',
           '本候选人按宽松匹配保留供比较，入选不代表已满足所有岗位条件。']
    if isinstance(score,(int,float)):lines.append(f'综合规则匹配分数：{score:.0%}。该分数不是录用概率。')
    lines+=['## 2. 技能匹配对照',
            '- 岗位要求：'+('、'.join(required) or '未设置必备技能'),
            '- 简历中找到文字依据：'+('、'.join(hits) or '未找到明确的必备技能文字依据'),
            '- 暂未找到明确依据：'+('、'.join(unknown) or '本次要求的技能均有文字匹配依据'),
            '- 加分技能文字命中：'+('、'.join(preferred) or '暂无'),
            '- 简历提取的技能：'+('、'.join(skills) or '未提供'),
            '技能文字命中只能说明简历提及，熟练程度与实际承担工作仍需面试核实。',
            '## 3. 项目与工作经历依据']
    projects=[p for p in coerce_to_list(meta.get('projects',[])) if isinstance(p,dict)]
    jobs=[p for p in coerce_to_list(meta.get('work_experience',[])) if isinstance(p,dict)]
    for project in projects[:4]:
        lines.append('- 项目：'+str(project.get('name') or '未命名项目')+'；简历描述：'+str(project.get('description') or '未提供具体描述')[:600])
    for job in jobs[:3]:
        lines.append('- 经历：'+str(job.get('company') or '单位未填写')+' / '+str(job.get('title') or '职位未填写')+'；'+str(job.get('description') or '未提供职责描述')[:400])
    if not projects and not jobs:lines.append('结构化记录未提供项目或工作经历，建议查看原始简历补充核实。')
    lines+=['## 4. 学历与经验对照',
            '- 岗位学历要求：'+str(query.required_education or '未设置'),
            '- 岗位经验要求：'+(str(query.min_experience_years)+' 年' if query.min_experience_years is not None else '未设置')]
    for edu in coerce_to_list(meta.get('education',[])):
        if isinstance(edu,dict):lines.append('- 简历教育记录：'+' / '.join(str(edu.get(k) or '未填写') for k in ('institution','major','degree')))
    lines.append('在校项目、实习与正式工作年限应分别核实，不能直接等同。')
    lines+=['## 5. 优势、差距与待核实信息']
    if hits:lines.append('- 可进一步了解的依据：简历提及了岗位要求中的'+ '、'.join(hits)+'，可围绕对应项目验证应用深度。')
    lines.extend('- '+n for n in resume.get('match_notes',[]))
    if unknown:lines.append('- 简历未明确提及'+ '、'.join(unknown)+'；信息缺失不等于不具备，请核实后再判断。')
    lines+=['- 建议结合原件核对项目时间、个人职责与结果数据，避免仅凭关键词判断。',
            '## 6. 面试建议',
            '1. 请选一个与岗位最相关的项目，说明个人职责、关键决策与可验证的结果。',
            '2. 请结合'+('、'.join(hits[:3]) or '岗位核心技能')+'说明实际使用场景、遇到的难点与解决过程。',
            '3. 对于'+('、'.join(unknown[:3]) or '尚未确认的岗位条件')+'，是否有未写进简历的实践依据？请提供具体例子。']
    return '\n\n'.join(lines)
