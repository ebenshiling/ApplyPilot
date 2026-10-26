"""Explicit per-place geography; legacy configuration remains readable."""
from __future__ import annotations

import re
import unicodedata

# Country identifiers accepted by the shipped python-jobspy runtime. Other
# country names remain valid location text for global boards/direct sources.
MARKETS = "argentina australia austria bahrain bangladesh belgium bulgaria brazil canada chile china colombia costa_rica croatia cyprus czech_republic denmark ecuador egypt estonia finland france germany greece hong_kong hungary india indonesia ireland israel italy japan kuwait latvia lithuania luxembourg malaysia malta mexico morocco netherlands new_zealand nigeria norway oman pakistan panama peru philippines poland portugal qatar romania saudi_arabia singapore slovakia slovenia south_africa south_korea spain sweden switzerland taiwan thailand turkey ukraine united_arab_emirates uk usa uruguay venezuela vietnam worldwide".split()
CODES = "ar au at bh bd be bg br ca cl cn co cr hr cy cz dk ec eg ee fi fr de gr hk hu in id ie il it jp kw lv lt lu my mt mx ma nl nz ng no om pk pa pe ph pl pt qa ro sa sg sk si za kr es se ch tw th tr ua ae gb us uy ve vn ww".split()


def normalized(value):
    text=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return re.sub(r'[^\w]+',' ',''.join(c for c in text if not unicodedata.combining(c)),flags=re.UNICODE).strip()


ALIASES={}
for key,code in zip(MARKETS,CODES):
    ALIASES[normalized(key.replace('_',' '))]=key
    ALIASES[code]=key
ALIASES.update({normalized(s):k for k,values in {'uk':['UK','GB','GBR','United Kingdom','Great Britain','England','Scotland','Wales','Northern Ireland'],
                                               'usa':['US','USA','United States','United States of America'],
                                               'worldwide':['Worldwide','Anywhere','Global'],
                                               'czech_republic':['Czechia'],'turkey':['Türkiye']}.items() for s in values})


def market(country):
    key=ALIASES.get(normalized(country))
    return key.replace('_',' ') if key else None


def country_label(value):
    key=ALIASES.get(normalized(value))
    return {'uk':'United Kingdom','usa':'United States','worldwide':'Worldwide'}.get(key,key.replace('_',' ').title() if key else str(value or '').strip())


def default_country(cfg):
    defaults=cfg.get('defaults') or {}
    if cfg.get('geography_enabled'):
        return cfg.get('country') or defaults.get('country_indeed') or defaults.get('country') or ''
    return defaults.get('country_indeed') or defaults.get('country') or cfg.get('country') or ''


def normalize_location(row,cfg=None):
    cfg=cfg or {}
    item=dict(row)
    raw=str(item.get('location') or '').strip()
    parts=[p.strip() for p in raw.split(',') if p.strip()]
    inferred=next((p for p in reversed(parts) if normalized(p) in ALIASES), '')
    country=str(item.get('country') or item.get('country_indeed') or inferred or default_country(cfg)).strip()
    remote=bool(item.get('remote',normalized(raw)=='remote'))
    structured=bool(item.get('city') or item.get('state') or 'country' in item or item.get('kind'))
    state=str(item.get('state') or (parts[-2] if len(parts)>=3 and inferred==parts[-1] else '')).strip()
    city=str(item.get('city') or '').strip()
    if not city and item.get('kind') not in {'country','region','remote'}:
        city=parts[0] if parts and normalized(raw) not in ALIASES and normalized(raw) not in {'remote','anywhere'} else ''
    kind=item.get('kind') or ('remote' if remote else 'city' if city else 'region' if state else 'country')
    if structured:
        query=', '.join(v for v in (city,state,country_label(country) if country and normalized(country)!='worldwide' else '') if v)
        if remote and not query:
            query='Remote'
    else:
        query=raw
    return item | {'location':query or raw or country_label(country),'label':item.get('label') or raw or query,
                   'country':country_label(country),'state':state,'city':city,'remote':remote,'kind':kind,
                   'provider_country':market(country),'structured':structured}


def locations(cfg):
    return [normalize_location(row if isinstance(row,dict) else {'location':str(row)},cfg) for row in cfg.get('locations',[]) or []]


def validate_locations(cfg):
    if cfg.get('location_filter_mode','legacy') not in {'legacy','selected'}:
        raise ValueError('Location filtering must use legacy pattern rules or selected locations')
    if cfg.get('location_filter_mode')=='selected' and not cfg.get('locations'):
        raise ValueError('Selected-location filtering needs at least one country, state, city or remote location')
    for i,row in enumerate(cfg.get('locations') or [],1):
        for key in ('country','state','city','kind','label'):
            if key in row and (not isinstance(row[key],str) or len(row[key])>160 or any(ord(c)<32 for c in row[key])):
                raise ValueError(f'locations[{i}].{key} must be text up to 160 characters')
        if row.get('kind') and row['kind'] not in {'country','region','city','remote'}:
            raise ValueError(f'locations[{i}].kind must be country, region, city or remote')
        if cfg.get('geography_enabled') and ('country' in row or row.get('city') or row.get('state')) and not (row.get('country') or default_country(cfg)):
            raise ValueError(f'locations[{i}] needs a country; choose Worldwide for global remote searches')


def contains(text,phrase):
    value=normalized(phrase)
    return bool(value and re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',normalized(text)))


class SelectedLocations(list):
    def __init__(self,cfg):
        self.places=locations(cfg)
        super().__init__([p.get('city') or p.get('state') or p['country'] or p['location'] for p in self.places])

    def matches(self,text,remote=None):
        if not text or normalized(text) in {'nan','unknown','not specified'}:
            return True
        is_remote=remote or any(contains(text,x) for x in ('remote','work from home','wfh','hybrid'))
        mentioned={key for alias,key in ALIASES.items() if len(alias)>2 and contains(text,alias)}
        if not mentioned:
            mentioned={key for alias,key in ALIASES.items() if len(alias)==2 and re.search(r'(?<!\w)'+re.escape(alias.upper())+r'(?!\w)',str(text))}
        for place in self.places:
            key=ALIASES.get(normalized(place['country']))
            if key=='worldwide' and not place['remote']:
                return True
            if place['remote']:
                if key == 'new_zealand' and contains(text, 'hybrid'):
                    continue  # hybrid is not an NZ remote-only match
                if is_remote and (key=='worldwide' or not mentioned or key in mentioned):
                    return True
                continue
            if mentioned and key and key not in mentioned:
                continue
            leaf=place.get('city') or place.get('state') or place['country'] or place['location']
            if contains(text,leaf):
                return True
            if place['kind']=='country' and key and key in mentioned:
                return True
        return False


def filter_config(cfg):
    return (SelectedLocations(cfg),[]) if cfg.get('location_filter_mode')=='selected' else None


def board_scope(place,boards):
    supported=list(boards)
    skipped=[]
    if not (place.get('provider_country') or market(place.get('country'))) and place.get('country'):
        for board in ('indeed','glassdoor'):
            if board in supported:
                supported.remove(board)
                skipped.append({'board':board,'reason':f"Country {place['country']} is not in this runtime's supported country markets"})
    if place.get('country') and market(place['country']) not in {'usa','canada'}:
        for board in ('zip_recruiter','ziprecruiter'):
            if board in supported:
                supported.remove(board)
                skipped.append({'board':board,'reason':'ZipRecruiter country coverage is limited to US/Canada in this integration'})
    return supported,skipped


def public_catalog():
    return [{'name':country_label(key.replace('_',' ')),'provider_country':key.replace('_',' ')} for key in MARKETS]


def source_country(site):
    from urllib.parse import urlsplit
    if site.get('country'):
        return country_label(site['country'])
    host=(urlsplit(site.get('url') or '').hostname or '').casefold()
    if host.endswith(('.co.uk','.gov.uk','.nhs.uk')) or host=='uk.indeed.com':
        return 'United Kingdom'
    if host.endswith('.ca') or host=='ca.indeed.com':
        return 'Canada'
    if host.endswith('.nz') or site.get('countries') == ['NZ']:
        return 'New Zealand'
    return ''


def smart_targets(sites,cfg,builder):
    from urllib.parse import quote_plus
    result=[]
    for site in sites:
        url=site.get('url','')
        scoped=source_country(site)
        dynamic=any('{'+key+'}' in url for key in ('location_encoded','location','country_encoded','state_encoded','city_encoded'))
        places=locations(cfg)
        eligible=[p for p in places if not scoped or not p['country'] or market(p['country'])=='worldwide' or normalized(scoped)==normalized(p['country'])]
        if not eligible and scoped:
            continue
        for place in eligible if dynamic else [eligible[0] if eligible else None]:
            local=cfg | {'_geography_expanded':True}
            if place:
                local['locations']=[{'location':place['location']}]
            for target in builder(sites=[site],search_cfg=local):
                if place:
                    for key in ('country','state','city'):
                        target['url']=target['url'].replace('{'+key+'_encoded}',quote_plus(place[key]))
                    target.update({key:place.get(key) for key in ('country','state','city','remote')})
                target['source_country']=scoped or 'Global / unspecified'
                result.append(target)
    return result
