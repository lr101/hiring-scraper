"""Frozen, synthetic profile-first cases, independent of matcher aliases.

Expected values come from explicit fixture specifications, never matcher output.
Layouts are intentionally varied; report both semantic examples and variants.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from html import escape
import json


JOBS = [
    {'id':'north-engineer','company':'North Lab','title':'Engineer','location':'Berlin','description':'Built reporting tools.','start':'2021-03','end':None,'current':True},
    {'id':'south-analyst','company':'South Works','title':'Analyst','location':'Munich','description':'Analyzed delivery data.','start':'2018','end':'2020','current':False},
    {'id':'north-manager','company':'North Lab','title':'Manager','location':'Bonn','description':'Managed projects.','start':'2016','end':'2018','current':False},
    {'id':'north-earlier','company':'North Lab','title':'Engineer','location':'Berlin','description':'Maintained tools.','start':'2010','end':'2014','current':False},
]
PROFILE = {'first_name':'Mara','last_name':'Neumann','email':'mara@example.invalid','phone':'+49301234567',
           'city':'Karlsruhe','country':'Germany','postal_code':'76131','street':'Example Street 4',
           'linkedin':'https://www.linkedin.com/in/mara-example','github':'https://github.com/mara-example',
           'website':'https://mara.example.invalid','portfolio':'https://portfolio.example.invalid',
           'experiences':[], 'preferences':{'available_from':'2026-12-01','weekly_hours':'32',
           'willing_to_work_onsite':True,'employment_type':'Part-time'}}


@dataclass
class Case:
    name: str
    family: str
    semantic_example: str
    html: str
    expected: dict[str, str | bool]
    initial: dict[str, str | bool] = field(default_factory=dict)
    profile: dict = field(default_factory=lambda: deepcopy(PROFILE))
    repeat: bool = True

    def __post_init__(self):
        # A support anchor must not duplicate a scored contact fact: that
        # would itself make a form ambiguous and contaminate wording scores.
        for label,value,kind in [('Portfolio',PROFILE['portfolio'],'text'),('Website',PROFILE['website'],'text'),('Email',PROFILE['email'],'email')]:
            if value not in self.expected.values():
                self.html=self.html.replace('<label>Email<input id=anchor type=email></label>',
                                            f'<label>{label}<input id=anchor type={kind}></label>')
                break


def control(label, identifier, layout, *, attrs='', value='', tag='input', options=''):
    attributes=f'id="{identifier}" {attrs}'
    if layout=='aria': attributes+=f' aria-label="{escape(label,quote=True)}"'
    if layout=='labelledby': attributes+=f' aria-labelledby="label-{identifier}"'
    if layout=='placeholder': attributes+=f' placeholder="{escape(label,quote=True)}"'
    if tag=='select': element=f'<select {attributes}>{options}</select>'
    elif tag=='textarea': element=f'<textarea {attributes}>{escape(value)}</textarea>'
    else: element=f'<input {attributes} value="{escape(value,quote=True)}">'
    if layout=='wrapped': return f'<label>{escape(label)}{element}</label>'
    if layout=='labelledby': return f'<span id="label-{identifier}">{escape(label)}</span>{element}'
    if layout=='adjacent': return f'<div><label>{escape(label)}</label>{element}</div>'
    return element


def document(body):
    # Unscored anchor ensures that unsupported target wording does not make the
    # entire fixture unrecognizable. No profile values are embedded in it.
    return '<meta charset="utf-8"><form><h1>Application</h1><label>Email<input id=anchor type=email></label><input type=file>'+body+'''<button>Submit</button></form>
      <script>window.submits=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submits++}</script>'''


def cases():
    result=[]
    labels=[
        ('Full name','Mara Neumann'),('Your complete name','Mara Neumann'),('Vor- und Nachname','Mara Neumann'),
        ('What is your full name?','Mara Neumann'),('First name','Mara'),('Given name','Mara'),('Forename','Mara'),
        ('Please enter your first name','Mara'),('Vorname (Pflichtfeld)','Mara'),('Given names','Mara'),
        ('Last name','Neumann'),('Family name','Neumann'),('Surname','Neumann'),('Nachname','Neumann'),
        ('Where do you live?','Karlsruhe'),('Which city do you live in?','Karlsruhe'),('What city are you based in?','Karlsruhe'),
        ('City / Town','Karlsruhe'),('Town or city','Karlsruhe'),('Your home town','Karlsruhe'),
        ('Current city of residence','Karlsruhe'),('In welcher Stadt leben Sie?','Karlsruhe'),('Wo wohnen Sie aktuell?','Karlsruhe'),
        ('Wohnsitz (Stadt)','Karlsruhe'),('Wohnort','Karlsruhe'),('Country of residence','Germany'),('Wohnland','Germany'),
        ('ZIP / Postal code','76131'),('Postal code / ZIP','76131'),('Post code','76131'),('Postleitzahl','76131'),
        ('Street and house number','Example Street 4'),('Straße / Hausnummer','Example Street 4'),('Address line 1','Example Street 4'),
        ('Contact phone number','+49301234567'),('Mobile / Cell phone','+49301234567'),('Phone (including country code)','+49301234567'),
        ('Mobiltelefonnummer','+49301234567'),('E-mail address','mara@example.invalid'),('Contact email address','mara@example.invalid'),
        ('Your e-mail address','mara@example.invalid'),('LinkedIn profile URL',PROFILE['linkedin']),('Link to your LinkedIn profile',PROFILE['linkedin']),
        ('GitHub profile',PROFILE['github']),('Personal website',PROFILE['website']),('Portfolio link',PROFILE['portfolio']),
        ('When could you start?','2026-12-01'),('Earliest possible start date','2026-12-01'),('Ab wann können Sie anfangen?','2026-12-01'),
        ('Hours per week','32'),('How many hours per week would you like to work?','32'),('Gewünschte Wochenstunden','32'),
    ]
    layouts=['wrapped','aria','labelledby','placeholder','adjacent']
    for index,(label,value) in enumerate(labels):
        for layout in layouts:
            result.append(Case(f'wording-{index}-{layout}','wording',label,
                document(control(label,'target',layout)),{'target':value}))
    negative=['Preferred name','Full legal name','Maiden name','Former surname','Name of your manager',
              'City where you want to work','Where would you like to live?','Place of birth','Nationality','Current salary',
              'Email of your reference','Phone number of your referee','Favorite city','Preferred interview location',
              'Confirm your consent','Password confirmation','Your signature','Emergency contact number',
              'Name of your pet','Name of your university']
    for index,label in enumerate(negative):
        for layout in layouts:
            result.append(Case(f'negative-{index}-{layout}','unrelated',label,
                document(control(label,'target',layout)),{'target':''}))
    for section in ['Reference','Emergency contact','Referenzperson','Notfallkontakt','Education','Ausbildung']:
        for wrapper in ['fieldset','aria','section','div-heading']:
            body=''.join(control(label,key,'wrapped',attrs='type=email' if key=='email' else '')
                         for label,key in [('Name','name'),('Email','email'),('Phone','phone'),('City','city')])
            if wrapper=='fieldset':body=f'<fieldset><legend>{section}</legend>{body}</fieldset>'
            elif wrapper=='aria':body=f'<div role=group aria-label="{section}">{body}</div>'
            else:body=f'<{("section" if wrapper=="section" else "div")}><h3>{section}</h3>{body}</{("section" if wrapper=="section" else "div")}>'
            result.append(Case(f'foreign-{section}-{wrapper}','foreign_sections',section,document(body),dict.fromkeys(['name','email','phone','city'],'')))
    confirmations=['Confirm email','Confirm email address','Re-enter your email address','Repeat your email',
                   'E-Mail-Adresse bestätigen','E-Mail wiederholen','Confirm phone number','Repeat phone number']
    for index,label in enumerate(confirmations):
        phone='phone' in label.lower()
        primary='Phone' if phone else 'Email'
        value=PROFILE['phone' if phone else 'email']
        for layout in layouts[:3]:
            body=control(primary,'primary',layout)+control(label,'confirmation',layout)
            result.append(Case(f'confirmation-{index}-{layout}','confirmations',label,document(body),{'primary':value,'confirmation':value}))
    conflicts=[('Last name','firstName'),('First name','lastName'),('City','postalCode'),('Email','phone'),('Phone','email')]
    for index,(label,name) in enumerate(conflicts):
        for layout in layouts[:3]:
            result.append(Case(f'conflict-{index}-{layout}','contradictory_hints',label,
                document(control(label,'target',layout,attrs=f'name="{name}"')),{'target':''}))
    for state,attrs,value,expected in [('readonly','readonly','Already entered','Already entered'),
            ('disabled','disabled','Already entered','Already entered'),('hidden','hidden','',''),
            ('existing','','Other City','Other City'),('aria-hidden','aria-hidden=true','','')]:
        for layout in layouts[:3]:
            result.append(Case(f'preserve-{state}-{layout}','existing_controls',state,
                document(control('City','target',layout,attrs=attrs,value=value)),{'target':expected},{'target':value}))
    for layout in layouts[:3]:
        for label in ['Employment type','Desired employment type','Beschäftigungsart']:
            result.append(Case(f'employment-{label}-{layout}','native_choices',label,
                document(control(label,'target',layout,tag='select',options='<option value="">Choose</option><option value=pt>Teilzeit</option><option value=ft>Vollzeit</option>')),
                {'target':'pt'}))
    for label in ['Are you willing to work on-site?','Are you open to working in the office?',
                  'Can you work from our office?','Sind Sie bereit vor Ort zu arbeiten?']:
        for answer in [True,False,None]:
            profile=deepcopy(PROFILE);profile['preferences']['willing_to_work_onsite']=answer
            result.append(Case(f'onsite-{label}-{answer}','native_choices',label,
                document(f'<fieldset><legend>{label}</legend><label><input id=yes type=radio name=onsite>Ja</label><label><input id=no type=radio name=onsite>Nein</label></fieldset>'),
                {'yes':answer is True,'no':answer is False},profile=profile))
    # Record assignments are explicitly specified. The evaluator never selects
    # expected source jobs by inspecting DOM values or running the solver.
    scenarios=[
        ('blank',[0,1],[{},{}],[0,1],False),
        ('reverse-company',[0,1],[{'company':'South Works'},{}],[1,0],False),
        ('reverse-both',[0,1],[{'company':'South Works'},{'company':'North Lab'}],[1,0],False),
        ('reverse-title',[0,1],[{'title':'Analyst'},{}],[1,0],False),
        ('duplicate-company-ambiguous',[0,2],[{'company':'North Lab'},{}],[None,None],True),
        ('global-resolution',[0,2],[{'company':'North Lab'},{'company':'North Lab','title':'Engineer'}],[2,0],False),
        ('duplicate-role-ambiguous',[0,3],[{'company':'North Lab','title':'Engineer'},{}],[None,None],True),
        ('date-disambiguates',[0,3],[{'company':'North Lab','title':'Engineer','start':'2010'},{}],[3,0],False),
        ('unknown-company',[0,1],[{'company':'Other Employer'},{}],[None,None],True),
        ('conflicting-location',[0,1],[{'company':'South Works','location':'Paris'},{}],[None,None],True),
        ('normalized-identity',[0,1],[{'company':' south works '},{}],[1,0],False),
        ('three-record-cycle',[0,1,2],[{'company':'South Works'},{'title':'Manager'},{}],[1,2,0],False),
    ]
    for name,job_indices,anchors,assignment,blocked in scenarios:
        for layout in ['fieldset','aria','nested']:
            profile=deepcopy(PROFILE);profile['experiences']=[deepcopy(JOBS[i]) for i in job_indices]
            rows=[];expected={};initial={}
            for row_index,anchor in enumerate(anchors):
                fields=[]
                for key,label in [('company','Employer'),('title','Job title'),('location','Location'),('start','Start date'),('end','End date')]:
                    identifier=f'r{row_index}-{key}';value=anchor.get(key,'')
                    fields.append(control(label,identifier,'wrapped',value=value))
                    initial[identifier]=value
                    target=assignment[row_index]
                    expected[identifier]=value if blocked or value else JOBS[target].get(key) or ''
                body=''.join(fields)
                if layout=='fieldset':body=f'<fieldset><legend>Employment {row_index+1}</legend>{body}</fieldset>'
                elif layout=='aria':body=f'<div role=group aria-label="Work experience {row_index+1}">{body}</div>'
                else:body=f'<div class=wrapper><div class=card>{body}</div></div>'
                rows.append(body)
            body='<section aria-label="Work experience"><div class=list>'+''.join(rows)+'</div></section>'
            result.append(Case(f'record-{name}-{layout}','record_identity',name,document(body),expected,initial,profile))
    for layout in ['fieldset','aria','section','div-heading']:
        body=control('First name','first','wrapped')+control('Email','later','wrapped')
        if layout=='fieldset':body=f'<fieldset id=changing><legend>Personal details</legend>{body}</fieldset>'
        elif layout=='aria':body=f'<div id=changing role=group aria-label="Personal details">{body}</div>'
        else:body=f'<{("section" if layout=="section" else "div")} id=changing><h3>Personal details</h3>{body}</{("section" if layout=="section" else "div")}>'
        body+='''<script>document.querySelector('#first').oninput=()=>{
          const group=document.querySelector('#changing');group.setAttribute('aria-label','Reference');group.setAttribute('role','group');
        }</script>'''
        result.append(Case(f'conditional-scope-{layout}','conditional_context','Section changes during fill',document(body),{'first':'Mara','later':''}))
    return result


def manifest():
    return [{'name':case.name,'family':case.family,'semantic_example':case.semantic_example,
             'html':case.html,'expected':case.expected,'initial':case.initial,'profile':case.profile,'repeat':case.repeat}
            for case in cases()]


if __name__=='__main__':
    corpus=manifest()
    print(json.dumps({'forms':len(corpus),'scored_fields':sum(len(case['expected']) for case in corpus),
                      'semantic_examples':len({(case['family'],case['semantic_example']) for case in corpus})},indent=2))
