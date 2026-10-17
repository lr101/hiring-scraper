"""Fictional application types for repeatable headless extension experiments."""
COMMON = '<meta charset="utf-8"><title>Job application experiment</title><style>body{font:16px system-ui;max-width:800px;margin:24px auto}label{display:block;margin:12px}input,textarea,select{padding:8px}iframe{height:100px}button{margin:10px}</style>'
STATE = '''<script>window.draft={submissions:0};document.querySelectorAll('input,textarea,select').forEach(el=>{el.addEventListener('input',()=>window.draft[el.id]=el.type==='checkbox'?el.checked:el.value);el.addEventListener('change',()=>window.draft[el.id]=el.type==='checkbox'?el.checked:el.value)});document.querySelectorAll('form').forEach(form=>form.onsubmit=e=>{e.preventDefault();window.draft.submissions++});</script>'''

CASES = {
    'personio-style': COMMON + '''<form><h1>Your application</h1><label>First<input name=first_name id=first aria-label=First></label><label>Last<input name=last_name id=last aria-label=Last></label><label>Email* (required)<input id=email name=email type=email></label><label>Phone* (required)<input id=phone name=phone></label><label>Academic title<select id=title><option value="">Please choose</option><option>Dr.</option></select></label><label>Nationality<input id=nationality></label><button type=submit>Send application</button></form>''' + STATE,
    'native-en': COMMON + '''<form><h1>Apply</h1>
      <label>First name<input id=first autocomplete=given-name required></label><label>Last name<input id=last autocomplete=family-name></label>
      <label>Email<input id=email type=email required></label><label>City<input id=city></label>
      <label>Country<select id=country><option value="">Choose</option><option value=DE>Germany</option><option value=FR>France</option></select></label>
      <label>Salary expectation<input id=salary required></label><label>Resume<input id=resume type=file required></label>
      <label><input id=consent type=checkbox required>Privacy consent</label><button type=submit>Submit</button></form>''' + STATE,
    'native-de': COMMON + '''<form><h1>Bewerbung</h1>
      <label>Vorname<input id=first></label><label>Nachname<input id=last></label><label>E-Mail-Adresse<input id=email type=email></label>
      <label>Wohnort<input id=city></label><label>Land<select id=country><option value="">Bitte wählen</option><option value=de>Deutschland</option></select></label>
      <label>Telefonnummer<input id=phone type=tel></label><label>Postleitzahl<input id=postal></label><button type=submit>Bewerbung absenden</button></form>''' + STATE,
    'lever-style': COMMON + '''<form><h1>Submit your application</h1>
      <label>Full name<input id=name autocomplete=name></label><label>Email<input id=email type=email></label><label>Phone<input id=phone type=tel></label>
      <label>LinkedIn URL<input id=linkedin></label><label>Cover letter<textarea id=letter></textarea></label>
      <label>Why this role?<textarea id=why required></textarea></label><label>Availability?<input id=availability></label>
      <label>Resume<input id=resume type=file></label><button type=submit>Submit application</button></form>''' + STATE,
    'custom-combobox': COMMON + '''<form><label>First name<input id=first></label><label>Email<input id=email type=email></label>
      <label>City<input id=city role=combobox aria-controls=cities aria-expanded=false></label><div id=cities role=listbox></div>
      <label>Country<select id=country><option value="">Choose</option><option>Germany</option></select></label>
      <button type=submit>Submit</button></form>''' + STATE + '''<script>const city=document.querySelector('#city');city.addEventListener('input',()=>{city.setAttribute('aria-expanded','true');setTimeout(()=>{document.querySelector('#cities').innerHTML='<div role="option">Heidelberg</div>';document.querySelector('[role=option]').onclick=()=>{city.value='Heidelberg';city.setAttribute('aria-expanded','false');window.draft.selectedCity=city.value;document.querySelector('#cities').replaceChildren()};},60)});</script>''',
    'ambiguous-and-embedded': COMMON + '''<form><label>First name<input id=first value="User correction"></label><label>Email<input id=email></label><label>Email<input id=other_email></label>
      <label>Phone<input id=phone readonly></label><label>Cover letter<textarea id=letter maxlength=3></textarea></label>
      <label>Optional reference<input id=reference></label><label><input id=consent type=checkbox>Privacy consent</label><button type=submit>Submit</button></form>
      <iframe title="External experience editor" srcdoc="&lt;label&gt;Employer&lt;input&gt;&lt;/label&gt;"></iframe><div id=shadow aria-label="Custom education editor"></div>''' + STATE + '''<script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<label>Degree<input></label>'</script>''',
    'validation-rejection': COMMON + '''<form><label>First name<input id=first></label><label>Email<input id=email type=email></label><label>Phone<input id=phone type=tel></label><button type=submit>Submit</button></form>''' + STATE + '''<script>document.querySelector('#phone').oninput=e=>{e.target.value='';window.draft.phoneRejected=true}</script>''',
}
