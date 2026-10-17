"""Offline employer-style forms: semantic markup, no extension-specific selectors."""
from .application_cases import COMMON, STATE
CONTACT='<label>First name<input id=first></label><label>Email<input id=email type=email></label>'
QUESTIONS='''<label>Website<input id=website type=url></label><label>Available from<input id=available type=date></label>
<label>Notice period<input id=notice></label><label>Expected annual salary (EUR)<input id=salary type=number></label>
<label>Hours per week<input id=hours type=number></label><label>Employment type<select id=employment><option value="">Choose</option><option>Part-time</option><option>Full-time</option></select></label>
<fieldset><legend>Are you authorized to work in Germany?</legend><label><input id=auth-yes type=radio name=auth value=yes>Yes</label><label><input id=auth-no type=radio name=auth value=no>No</label></fieldset>
<label>Do you require visa sponsorship?<select id=sponsor><option value="">Choose</option><option value=true>Yes</option><option value=false>No</option></select></label>
<label>Willing to relocate?<select id=relocate><option value="">Choose</option><option value=y>Yes</option><option value=n>No</option></select></label>
<label>Resume<input id=resume type=file required></label><label><input id=consent type=checkbox required>I accept the privacy policy</label>'''

def history(split=False,broken=False):
    # Delayed inline insertion resembles controlled framework forms; the engine
    # has to rediscover each group, rather than cache DOM nodes or infer CV prose.
    row='''<fieldset><legend>Employment INDEX</legend><label>Employer<input name=company></label><label>Job title<input name=title></label><label>Location<input name=location></label><label>Responsibilities<textarea name=description></textarea></label>'''
    if split:
        months='<option value="">Choose</option>'+''.join(f'<option value="{i:02}">{name}</option>' for i,name in enumerate(['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'],1))
        row+=f'<label>Start month<select name=start_month>{months}</select></label><label>Start year<input name=start_year></label><label>End month<select name=end_month>{months}</select></label><label>End year<input name=end_year></label>'
    else:
        row+='<label>Start date<input type=month name=start></label><label>End date<input type=month name=end></label>'
    row+='<label><input name=current type=checkbox>I currently work here</label></fieldset>'
    import json
    initial=row.replace('INDEX','1')
    action="document.querySelector('#work').insertAdjacentHTML('beforeend',row.replace('INDEX',++count))" if not broken else "document.querySelector('#work').insertAdjacentHTML('beforeend','<dialog open><p>Unsupported employer-specific editor</p><button type=button>Save</button></dialog>')"
    return f'''<section id=work aria-label="Work experience"><h2>Work experience</h2>{initial}<button id=add-work type=button>Add experience</button></section><script>let count=1;const row={json.dumps(row)};document.querySelector('#add-work').onclick=()=>setTimeout(()=>{{{action}}},40);</script>'''

CASES={
 'common': COMMON+'<form>'+CONTACT+QUESTIONS+'<button type=submit>Submit</button></form>'+STATE,
 'history-month': COMMON+'<form>'+CONTACT+history()+'<button type=submit>Submit</button></form>'+STATE,
 'history-split': COMMON+'<form>'+CONTACT+history(True)+'<button type=submit>Submit</button></form>'+STATE,
 'history-broken': COMMON+'<form>'+CONTACT+history(broken=True)+'<button type=submit>Submit</button></form>'+STATE,
 'question-limits':COMMON+'<form>'+CONTACT+'''<label>Salary expectation<input id=salary type=number></label><label>Expected annual salary (USD)<input id=usd type=number></label><label>Are you authorized to work in the United States?<select id=auth><option value="">Choose</option><option>Yes</option><option>No</option></select></label><label>Do you require visa sponsorship?<select id=sponsor><option value="">Choose</option><option>No sponsorship required</option><option>I will need support later</option></select></label><label>Cover letter<textarea id=letter></textarea></label><button type=submit>Submit</button></form>'''+STATE,
}
