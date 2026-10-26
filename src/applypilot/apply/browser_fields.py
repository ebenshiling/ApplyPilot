"""Small DOM readers shared by form inspection and explicit question assistance."""

FIELD_HELPERS = r"""
  const fieldSelector='input,textarea,select,[contenteditable="true"],[role="textbox"]';
  const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';};
  const text=el=>(el?.innerText||el?.textContent||'').replace(/\s+/g,' ').trim();
  const named=ids=>(ids||'').split(/\s+/).map(id=>text(document.getElementById(id))).filter(Boolean).join(' ');
  const nodes=(root=document)=>{const fields=[...root.querySelectorAll(fieldSelector)];for(const el of root.querySelectorAll('*'))if(el.shadowRoot)fields.push(...nodes(el.shadowRoot));return fields;};
  const documentToken=window.__applypilotDocumentToken ||= (crypto.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2));
  const describe=el=>{
    const type=(el.type||'').toLowerCase(),tag=el.tagName.toLowerCase();
    let label=[...(el.labels||[])].map(text).filter(Boolean).join(' ')||named(el.getAttribute('aria-labelledby'))||el.getAttribute('aria-label')||'';
    const wrapper=el.closest('[data-question-id],[data-testid*="question"],.form-group,.field,.question,.application-question,[role="group"],fieldset');
    const heading=wrapper?.querySelector('legend,label,h2,h3,h4,[data-testid*="label"]');
    if(!label)label=text(heading);
    if(!label){let node=el;for(let level=0;level<3&&!label&&node;level++,node=node.parentElement){const prev=node.previousElementSibling;if(prev&&!prev.querySelector(fieldSelector)&&!prev.matches(fieldSelector))label=text(prev).slice(0,1200);}}
    const help=named(el.getAttribute('aria-describedby'))||text(wrapper?.querySelector('.help,.hint,.description,[data-testid*="description"],small'));
    const editable=el.isContentEditable;
    const token=el.dataset.applypilotField ||= documentToken+':'+(window.__applypilotFieldSequence=(window.__applypilotFieldSequence||0)+1);
    return {token,document_token:documentToken,tag,type:type||(editable?'contenteditable':'text'),id:el.id||'',name:el.name||'',
      label:(label||el.placeholder||el.name||'Unlabelled field').slice(0,1200),help:help.slice(0,2000),placeholder:el.placeholder||'',
      value:type==='password'?'[private]':String(editable?el.innerText:(el.value||'')),required:!!el.required||el.getAttribute('aria-required')==='true',
      valid:el.validity?.valid??true,autocomplete:el.autocomplete||'',checked:!!el.checked,disabled:!!el.disabled,readonly:!!el.readOnly,
      group_label:text(el.closest('fieldset')?.querySelector('legend'))||text(wrapper?.querySelector('h2,h3,h4,[data-testid*="label"]')),
      maxlength:el.maxLength>0?el.maxLength:null,options:tag==='select'?[...el.options].map(o=>({value:o.value,label:o.label})):[]};
  };
"""

FOCUSED_FIELD = "() => {" + FIELD_HELPERS + r"""
  if(!document.hasFocus())return null;
  let el=document.activeElement;while(el?.shadowRoot?.activeElement)el=el.shadowRoot.activeElement;
  return el?.matches(fieldSelector)&&visible(el)?describe(el):null;
}"""

QUESTION_FIELDS = "() => {" + FIELD_HELPERS + r"""
  return nodes().filter(el=>visible(el)&&!el.disabled&&!['password','hidden','file','button','submit','reset','image'].includes(el.type)).map(describe);
}"""

# Only the document receiving focus/input reports its field; no iframe sweep.
FOCUS_TRACKER = "(() => {" + FIELD_HELPERS + r"""
  let timer;
  const emit=()=>{
    let el=document.activeElement;while(el?.shadowRoot?.activeElement)el=el.shadowRoot.activeElement;
    const field=document.hasFocus()&&el?.matches(fieldSelector)&&visible(el)&&el.type!=='password'?describe(el):null;
    window.__applypilotFocusChanged?.(field).catch(()=>{});
  };
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(emit,35);};
  for(const name of ['focusin','focusout','input','change'])document.addEventListener(name,schedule,true);
})()"""
