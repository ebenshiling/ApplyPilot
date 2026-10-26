"""Saved application values and conservative local form matching."""
from __future__ import annotations

import re

LOCAL_ONLY={'date_of_birth','nationality','country_of_birth','gender','pronouns','race_ethnicity','disability_status','reasonable_adjustments','adjustment_details','religion_belief','sexual_orientation','marital_status','veteran_status'}


def _text(value):
    return str(value if value is not None else '').strip()


def contact_values(personal):
    full=_text(personal.get('full_name'))
    parts=full.split()
    first=_text(personal.get('first_name',parts[0] if parts else ''))
    middle=_text(personal.get('middle_names',' '.join(parts[1:-1])))
    last=_text(personal.get('last_name',parts[-1] if len(parts)>1 else ''))
    line1=_text(personal.get('address_line1',personal.get('address','')))
    values={key:_text(personal.get(key)) for key in ('title','preferred_name','name_suffix','email','phone','mobile','alternative_phone','city','county','country','country_code','postal_code','linkedin_url','github_url','website_url','portfolio_url')}
    values.update(first_name=first,middle_names=middle,last_name=last,full_name=full or ' '.join(p for p in (first,middle,last) if p),
                  address=line1,address_line1=line1,address_line2=_text(personal.get('address_line2')),address_line3=_text(personal.get('address_line3')),
                  province_state=_text(personal.get('province_state',personal.get('state',''))))
    values['full_address']='\n'.join(values[k] for k in ('address_line1','address_line2','address_line3','city','county','province_state','postal_code','country') if values[k])
    return values


def application_values(profile, *, country_code=None, country_preferences=None):
    if country_code is not None:
        from copy import deepcopy
        from applypilot.country_context import normalize_country
        code = normalize_country(country_code, optional=False)
        profile = deepcopy(profile)
        if code == "NZ":
            # Never answer NZ questions from a legacy/global UK yes/no value.
            profile["work_authorization"] = dict((country_preferences or {}).get("work_authorization") or {})
            profile["availability"] = dict(((country_preferences or {}).get("application_operations") or {}).get("availability") or {})
            profile["compensation"] = dict(((country_preferences or {}).get("search_preferences") or {}).get("compensation") or {})
            for key in ("legally_authorized_to_work", "require_sponsorship", "work_permit_type", "permit_expiry_date", "willing_to_relocate", "willing_to_travel", "notice_period", "earliest_start_date", "available_for_full_time", "available_for_part_time", "available_for_contract", "available_for_shifts", "driving_licence", "own_transport", "salary_expectation", "salary_currency", "salary_range_min", "salary_range_max"):
                profile.setdefault("personal", {}).pop(key, None)
            expiry=profile["work_authorization"].get("permit_expiry_date")
            if expiry:
                from datetime import date
                try:expired=date.fromisoformat(str(expiry))<date.today()
                except ValueError:expired=True
                if expired:
                    for key in ("legally_authorized_to_work","require_sponsorship","work_permit_type","permit_expiry_date"):
                        profile["work_authorization"].pop(key,None)
    personal=profile.get('personal',{})
    values=contact_values(personal)
    values.update({k:_text(personal.get(k)) for k in ('date_of_birth','nationality','country_of_birth')})
    groups={
        'work_authorization':('legally_authorized_to_work','require_sponsorship','work_permit_type','permit_expiry_date','driving_licence','own_transport'),
        'availability':('earliest_start_date','notice_period','available_for_full_time','available_for_part_time','available_for_contract','available_for_shifts','willing_to_relocate','willing_to_travel'),
        'compensation':('salary_expectation','salary_currency','salary_range_min','salary_range_max'),
        'experience':('years_of_experience_total','education_level','current_job_title','current_company'),
        'eeo_voluntary':('gender','pronouns','race_ethnicity','disability_status','reasonable_adjustments','adjustment_details','veteran_status','religion_belief','sexual_orientation','marital_status'),
    }
    for group,keys in groups.items():
        section=profile.get(group,{})
        values.update({key:_text(section.get(key,personal.get(key,''))) for key in keys})
    return {k:v for k,v in values.items() if v}


def normalize(value):
    value=re.sub(r'([a-z])([A-Z])',r'\1 \2',_text(value))
    return re.sub(r'[^a-z0-9]+',' ',value.casefold()).strip()


def field_value_key(field):
    words=normalize(' '.join(_text(field.get(k)) for k in ('group_label','label','placeholder','name','id')))
    if re.search(r'password|verification code|one time|privacy|consent|terms|acknowledge|agree|declar|confirm.*accurat',words):
        return None
    autocomplete=_text(field.get('autocomplete')).lower().split()
    tokens={'honorific-prefix':'title','given-name':'first_name','additional-name':'middle_names','family-name':'last_name','name':'full_name',
            'email':'email','tel':'phone','address-line1':'address_line1','address-line2':'address_line2','address-line3':'address_line3',
            'street-address':'full_address','address-level2':'city','address-level1':'province_state','postal-code':'postal_code','country-name':'country','country':'country_code','bday':'date_of_birth','sex':'gender'}
    for token in reversed(autocomplete):
        if token in tokens:
            return tokens[token]
    patterns=[
        ('current_job_title',r'current.*(?:job|role|position).*title|job title|current position'),
        ('current_company',r'current.*(?:company|employer)|current employer'),
        ('preferred_name',r'preferred (?:first )?name|known as'),
        ('first_name',r'first name|given name|forename'),('middle_names',r'middle names?'),('last_name',r'last name|family name|surname'),
        ('full_name',r'full name|legal name'),('title',r'\b(?:salutation|honorific)\b|(?:^| )title(?: |$)'),
        ('name_suffix',r'name suffix|suffix'),('date_of_birth',r'date of birth|birth date|\bdob\b'),('country_of_birth',r'country of birth'),('nationality',r'nationality|citizenship'),
        ('address_line3',r'address (?:line )?3|third.*address'),('address_line2',r'address (?:line )?2|second.*address'),
        ('full_address',r'full (?:postal )?address|complete address'),('address_line1',r'address (?:line )?1|street address|house.*street|^address$'),
        ('postal_code',r'post ?code|postal code|zip code|\bzip\b'),('city',r'\bcity\b|\btown\b'),('county',r'\bcounty\b'),('province_state',r'\bprovince\b|\bstate\b|\bregion\b'),('country',r'\bcountry\b'),
        ('alternative_phone',r'alternative.*(?:phone|telephone)|other.*phone'),('mobile',r'\bmobile\b|cell phone'),('phone',r'\bphone\b|telephone|contact number'),('email',r'\be ?mail\b'),
        ('linkedin_url',r'linkedin'),('github_url',r'github'),('portfolio_url',r'portfolio'),('website_url',r'website|personal site'),
        ('require_sponsorship',r'(?:require|need).*sponsor|sponsorship required'),('legally_authorized_to_work',r'authori[sz]ed.*work|right to work|eligible to work'),
        ('permit_expiry_date',r'(?:visa|permit).*expir'),('work_permit_type',r'(?:visa|permit) type|type of (?:visa|permit)|immigration status'),
        ('driving_licence',r'driving licen[cs]e|driver.s licen[cs]e'),('own_transport',r'own transport'),
        ('notice_period',r'notice period'),('earliest_start_date',r'start date|available.*start|availability date'),('willing_to_relocate',r'willing.*relocat'),('willing_to_travel',r'willing.*travel'),
        ('available_for_full_time',r'available.*full time'),('available_for_part_time',r'available.*part time'),('available_for_contract',r'available.*contract'),('available_for_shifts',r'available.*shifts|willing.*shift'),
        ('salary_expectation',r'expected.*salary|salary expect|desired salary'),('salary_currency',r'salary currency'),('years_of_experience_total',r'years.*experience'),('education_level',r'highest.*(?:education|qualification)|education level'),
        ('gender',r'\bgender\b|\bsex\b'),('pronouns',r'pronouns'),('race_ethnicity',r'ethnic|\brace\b'),
        ('adjustment_details',r'adjustment.*(?:detail|describe|specify)'),('reasonable_adjustments',r'reasonable adjustments|require.*adjustment'),
        ('disability_status',r'disabilit'),('veteran_status',r'veteran'),('religion_belief',r'religion|religious|belief'),('sexual_orientation',r'sexual orientation'),('marital_status',r'marital'),
    ]
    for key,pattern in patterns:
        if re.search(pattern,words):
            if key in {'require_sponsorship','legally_authorized_to_work'} and re.search(r'\bnot\b|\bwithout\b',words):
                return None
            return key
    return None


def _equivalent(value):
    text=normalize(value)
    aliases={
        'yes':{'yes','true','1'},'no':{'no','false','0'},
        'male':{'male','man','m'},'female':{'female','woman','f'},
        'prefer not to say':{'prefer not to say','prefer not to answer','decline to self identify','decline to answer','i do not wish to answer','prefer not to disclose'},
    }
    return next((choices for choices in aliases.values() if text in choices),{text})


def empty_field(field):
    if field.get('type')=='radio':
        return not field.get('checked')
    if field.get('tag')=='select' and field.get('value'):
        current=next((o for o in field.get('options',[]) if o['value']==field['value']),{})
        return bool(re.match(r'^(?:please )?(?:select|choose|pick)\b',normalize(current.get('label'))))
    return not field.get('value')


def local_fills(profile,fields,verified=(),*,country_code=None,country_preferences=None):
    if country_code is not None:
        from applypilot.country_context import normalize_country
        country_code=normalize_country(country_code,optional=False)
    values=application_values(profile,country_code=country_code,country_preferences=country_preferences)
    actions=[]
    checked_groups={f.get('name') for f in fields if f.get('type')=='radio' and f.get('checked')}
    for field in fields:
        if field.get('type') in {'password','file','hidden','submit','button','checkbox'} or not empty_field(field):
            continue
        if field.get('type')=='radio' and field.get('name') in checked_groups:
            continue
        value_key=field_value_key(field)
        if country_code=="NZ" and value_key in {'require_sponsorship','legally_authorized_to_work','permit_expiry_date','work_permit_type'}:
            scope=normalize(' '.join(_text(field.get(k)) for k in ('group_label','label','placeholder','name')))
            if not re.search(r'\b(?:nz|new zealand)\b',scope) or re.search(r'\b(?:uk|united kingdom|australia|united states|canada|without|unless|if|conditional|unrestricted)\b',scope):
                continue
        value=values.get(value_key)
        if not value and value_key=='country_code' and field.get('tag')=='select':
            value=values.get('country')
        if not value and field.get('type')!='radio' and country_code!="NZ":
            exact=normalize(field.get('label'))
            answer=next((a for a in verified if normalize(a['question'])==exact and exact),None)
            value=_text(answer['answer']) if answer else None
        if not value or not (field.get('id') or field.get('name')):
            continue
        by='id' if field.get('id') else 'name'
        action={'by':by,'key':field[by],'value':value,'method':'fill'}
        if field.get('tag')=='select':
            options=[o for o in field.get('options',[]) if normalize(o['label']) in _equivalent(value) or normalize(o['value']) in _equivalent(value)]
            if len(options)!=1:
                continue
            action.update(method='select',value=options[0]['value'])
        elif field.get('type')=='radio':
            if not field.get('group_label') or not field.get('id'):
                continue
            if not ({normalize(field.get('label')),normalize(field.get('value'))}&_equivalent(value)):
                continue
            action.update(method='check',value='')
        actions.append(action)
    return actions
