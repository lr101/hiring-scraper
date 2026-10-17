"""Fictional two-page career history for repeatable document layout tests."""
from html import escape
from io import BytesIO
import re
from zipfile import ZipFile, ZIP_DEFLATED

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def run(text, bold=False, color=None):
    properties = ('<w:b/>' if bold else '') + (f'<w:color w:val="{color}"/>' if color else '')
    return f'<w:r><w:rPr>{properties}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(text='', style='Normal', *, page_break=False, bullet=False, runs=None):
    settings = f'<w:pStyle w:val="{style}"/>'
    if page_break: settings += '<w:pageBreakBefore/>'
    if bullet: settings += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    return f'<w:p><w:pPr>{settings}</w:pPr>{runs if runs is not None else run(text)}</w:p>'


def heading(text, page_break=False):
    return paragraph(text, 'Heading1', page_break=page_break)


def role(dates, title, company, bullets):
    date = paragraph(dates, 'Dates')
    content = paragraph(title, 'Role') + paragraph(company, 'Employer')
    content += ''.join(paragraph(item, bullet=True) for item in bullets)
    return f'''<w:tbl><w:tblPr><w:tblW w:w="10106" w:type="dxa"/><w:tblLayout w:type="fixed"/>
<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/><w:left w:w="0" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr>
<w:tblGrid><w:gridCol w:w="1800"/><w:gridCol w:w="8306"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>
<w:tc><w:tcPr><w:tcW w:w="1800" w:type="dxa"/></w:tcPr>{date}</w:tc><w:tc><w:tcPr><w:tcW w:w="8306" w:type="dxa"/></w:tcPr>{content}</w:tc></w:tr></w:tbl>'''


SUMMARY = ('Project manager with eleven years of experience delivering research, engineering and digitalization projects. '
           'Connects technical teams, finance and external partners through clear plans, reliable reporting and practical governance. '
           'Experienced in public funding, multi-partner work packages and translating complex requirements into measurable delivery.')
FIRST_BULLET = ('Lead a portfolio of six research and digitalization projects with a combined budget of €2.4 million, coordinating '
                'engineering, university partners and finance from proposal preparation through final reporting.')


def realistic_docx():
    body = paragraph('Mara Neumann', 'Name') + paragraph('Project Manager | Research, Funding & Digitalization', 'Subtitle')
    body += paragraph('Karlsruhe, Germany  ·  mara.neumann@example.com  ·  +49 721 000000', 'Contact')
    body += paragraph(style='Contact', runs='<w:hyperlink r:id="portfolio">' + run('mara-neumann.example.com', color='277A78') + '</w:hyperlink>')
    body += heading('Professional profile') + paragraph(SUMMARY)
    body += heading('Core expertise') + paragraph('Research project delivery  •  Public funding & grants  •  Budget control  •  Stakeholder coordination  •  Process improvement  •  Risk management')
    body += heading('Professional experience')
    body += role('2021–present', 'Senior Project Manager', 'Rheinbogen Systems GmbH · Karlsruhe', [
        FIRST_BULLET,
        'Prepare funding applications, milestone plans and audit-ready cost reports; secured €780,000 in approved funding across three successful consortium proposals.',
        'Introduced a shared project dashboard and monthly risk reviews, reducing overdue actions by 28% while improving visibility for sponsors and project leads.',
        'Facilitate workshops with up to 18 stakeholders in Karlsruhe, München and Strasbourg, turning technical discussions into agreed decisions and accountable next steps.',
    ])
    body += role('2017–2021', 'Project Coordinator', 'Westhafen Engineering GmbH · Mannheim', [
        'Coordinated engineering change projects across product development, purchasing and production, maintaining integrated schedules for teams of eight to twelve people.',
        'Managed supplier documentation, tracked purchase orders and reconciled project costs; supported delivery of a €620,000 equipment modernization program on schedule.',
        'Standardized status reports and handover checklists, reducing repeated clarification requests and making responsibilities transparent across departmental boundaries.',
    ])
    body += role('2015–2017', 'Graduate Project Analyst', 'Elbwinkel Technology Services · Stuttgart', [
        'Built Excel-based project cost models, prepared steering-committee material and maintained action logs for industrial digitalization initiatives.',
        'Mapped administrative workflows with process owners and supported rollout of a document management system used by 45 colleagues.',
    ])
    body += heading('Selected projects', page_break=True)
    body += paragraph('Connected Lab Network · 2023–2025', 'Role')
    body += paragraph('Multi-partner research collaboration involving an engineering SME and two university laboratories. Coordinated work packages, budget forecasts, partner meetings and reporting obligations across a €940,000 program.')
    body += paragraph('Delivered all five milestones on schedule and established a shared evidence register for technical results, procurement records and financial documentation.', bullet=True)
    body += paragraph('Funding Operations Toolkit · 2022–2023', 'Role')
    body += paragraph('Designed reusable funding checklists, cost templates and reporting schedules for project leads. Connected eligibility checks with project setup to avoid inconsistent assumptions during delivery.')
    body += paragraph('Reduced reporting preparation time from five working days to three, with clear ownership for evidence gathering and budget reconciliation.', bullet=True)
    body += paragraph('Production Data Pilot · 2019–2020', 'Role')
    body += paragraph('Coordinated a cross-functional pilot that connected shop-floor data with project reporting. Organized requirements sessions, acceptance criteria and practical training for production coordinators.')
    body += paragraph('Education', 'Heading1')
    body += role('2013–2015', 'M.Sc. Industrial Engineering & Management', 'Karlsruhe Institute of Technology', [
        'Focus: technology management, operations and innovation. Master’s thesis on adoption of digital tools in engineering project teams. Final grade: 1.7.',
    ])
    body += role('2009–2013', 'B.Sc. Industrial Engineering', 'Hochschule Mannheim', [
        'Focus: production systems, business administration and quantitative methods. Semester project on resource planning for a university research laboratory.',
    ])
    body += heading('Professional development')
    body += paragraph('PRINCE2 Practitioner · 2022  |  Professional Scrum Master I · 2020  |  Public funding and grant administration · 2023')
    body += heading('Tools & languages')
    body += paragraph(style='Normal', runs=run('Tools  ', bold=True) + run('MS Project, Jira, Confluence, Excel / Power Query, Power BI, SharePoint, Miro; working knowledge of SQL.'))
    body += paragraph(style='Normal', runs=run('Languages  ', bold=True) + run('German: native  |  English: C1  |  French: B1.'))
    body += heading('Community involvement')
    body += paragraph('Volunteer mentor, Karlsruhe Women in Engineering network · Since 2021. Support early-career engineers with project planning, stakeholder communication and structured preparation for professional presentations.')
    body += f'''<w:sectPr><w:headerReference w:type="default" r:id="header"/><w:footerReference w:type="default" r:id="footer"/>
<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1050" w:right="900" w:bottom="1000" w:left="900" w:header="420" w:footer="420"/></w:sectPr>'''
    styles = f'''<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/><w:sz w:val="21"/><w:color w:val="263A44"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="90" w:line="260" w:lineRule="auto"/><w:widowControl/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Name"><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:sz w:val="60"/><w:color w:val="183C4B"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="100"/></w:pPr><w:rPr><w:sz w:val="24"/><w:color w:val="277A78"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Contact"><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="40"/></w:pPr><w:rPr><w:sz w:val="18"/><w:color w:val="536774"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="190" w:after="100"/><w:pBdr><w:bottom w:val="single" w:sz="5" w:color="A5C9C6" w:space="4"/></w:pBdr></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="183C4B"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Role"><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="70" w:after="45"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Employer"><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="85"/></w:pPr><w:rPr><w:i/><w:sz w:val="19"/><w:color w:val="536774"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Dates"><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="19"/><w:color w:val="536774"/></w:rPr></w:style></w:styles>'''
    footer = f'''<w:ftr xmlns:w="{W}"><w:p><w:pPr><w:tabs><w:tab w:val="right" w:pos="10106"/></w:tabs><w:spacing w:before="90"/><w:pBdr><w:top w:val="single" w:sz="4" w:color="A5C9C6"/></w:pBdr></w:pPr><w:r><w:rPr><w:sz w:val="17"/><w:color w:val="536774"/></w:rPr><w:t>Mara Neumann · Project Management</w:t><w:tab/><w:t xml:space="preserve">Page </w:t></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple><w:r><w:t xml:space="preserve"> of </w:t></w:r><w:fldSimple w:instr="NUMPAGES"><w:r><w:t>2</w:t></w:r></w:fldSimple></w:p></w:ftr>'''
    parts = {
        '[Content_Types].xml': '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>''' + ''.join(f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>' for name, kind in [('document','document.main'),('styles','styles'),('numbering','numbering'),('header1','header'),('footer1','footer'),('settings','settings')]) + '</Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="{name}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}"/>' for name, kind, target in [('header','header','header1.xml'),('footer','footer','footer1.xml'),('styles','styles','styles.xml'),('numbering','numbering','numbering.xml'),('settings','settings','settings.xml')]) + '<Relationship Id="portfolio" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://mara-neumann.example.com" TargetMode="External"/></Relationships>',
        'word/document.xml': f'<w:document xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>{body}</w:body></w:document>',
        'word/styles.xml': re.sub(r'(w:styleId="([^"]+)">)(?!<w:name)', r'\1<w:name w:val="\2"/>', styles),
        'word/numbering.xml': f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="220"/></w:tabs><w:ind w:left="220" w:hanging="220"/></w:pPr><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/></w:rPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>',
        'word/header1.xml': f'<w:hdr xmlns:w="{W}">{paragraph(style="Contact", runs=run("MARA NEUMANN  /  PROJECT MANAGEMENT", color="277A78"))}</w:hdr>',
        'word/footer1.xml': footer,
        'word/settings.xml': f'<w:settings xmlns:w="{W}"><w:compat/><w:updateFields w:val="true"/></w:settings>',
    }
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text.encode())
    return output.getvalue()


def tailored_changes(paragraphs, variant='tailored'):
    replacements = {
        SUMMARY: 'Project manager specializing in publicly funded research and digitalization. Eleven years connecting engineering teams, university partners and finance through structured delivery, reliable reporting and governance. Experienced in consortium proposals, funding compliance and turning complex requirements into clear plans and measurable outcomes.',
        FIRST_BULLET: 'Manage six research and digitalization projects with a combined €2.4 million budget, aligning engineering, university partners and finance from consortium proposals to milestones and final reports.',
        'Project Manager | Research, Funding & Digitalization': 'Project Manager | Research & Funding Programs',
    }
    changes = []
    for row in paragraphs:
        value = replacements.get(row['text'])
        if variant == 'expanded' and len(row['text']) > 120:
            value = (value or row['text']) + ' Maintain clear responsibilities and coordinated communication across all project partners.'
        elif variant == 'stress' and len(row['text']) > 120:
            value = (value or row['text']) + ' ' + ('Maintain clear responsibilities, funding documentation and coordinated communication across all project partners. ' * 3).strip()
        if value is not None:
            changes.append({'id': row['id'], 'text': value})
    return changes
