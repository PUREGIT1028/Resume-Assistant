"""Export extracted text with an embedded Chinese font and honest provenance."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from threading import Lock
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
_lock=Lock()
def build_resume_pdf(record):
    with _lock:
        if 'ResumeChinese' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('ResumeChinese',r'C:\Windows\Fonts\simhei.ttf'))
    out=BytesIO()
    doc=SimpleDocTemplate(out,pagesize=(595.28,841.89),leftMargin=45,rightMargin=45,topMargin=45,bottomMargin=45)
    body=ParagraphStyle('body',fontName='ResumeChinese',fontSize=11,leading=18,wordWrap='CJK',spaceAfter=5,splitLongWords=True)
    title=ParagraphStyle('title',parent=body,fontSize=17,leading=26,spaceAfter=12)
    story=[Paragraph(escape(Path(record.get('filename','简历')).stem),title),Paragraph('文字重建版：根据提取文字生成，不保留原始简历版式。',body),Spacer(1,16)]
    text=record.get('text','') or '暂无可导出的文字'
    text=''.join('-' if 0xE000<=ord(c)<=0xF8FF else c for c in text)
    for line in text.splitlines():
        story.append(Paragraph(escape(line),body) if line.strip() else Spacer(1,6))
    def footer(c,d):
        c.setFont('ResumeChinese',8)
        c.drawString(45,24,'拾才 · 提取文字重建版')
        c.drawRightString(550,24,str(d.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
