"""Explicit bounded dashboard runs; legacy CLI discovery keeps its existing path."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import re
import threading

_budget = ContextVar('discovery_budget', default=None)
_selection = ContextVar('processing_selection', default=None)


def validate_policy(value):
    if not isinstance(value, dict):
        raise ValueError('Choose the discovery limits and processing scope')
    result = {'max_new_jobs':50, 'results_per_search':10, 'title_match':True, 'processing_scope':'new','explicit_picks':False} | value
    if set(result) - {'max_new_jobs','results_per_search','title_match','processing_scope','saved_job_urls','explicit_picks'}:
        raise ValueError('Unsupported run-scope option')
    for key, high in (('max_new_jobs',500),('results_per_search',50)):
        if isinstance(result[key],bool) or not isinstance(result[key],int) or not 1 <= result[key] <= high:
            raise ValueError(f'{key} must be between 1 and {high}')
    if not isinstance(result['title_match'],bool) or not isinstance(result['explicit_picks'],bool) or result['processing_scope'] not in {'new','matching'}:
        raise ValueError('Choose matching-title behavior and new or matching saved jobs')
    urls = result.get('saved_job_urls',[])
    if not isinstance(urls,list) or len(urls)>500 or any(not isinstance(u,str) or not u for u in urls):
        raise ValueError('Invalid captured saved-job selection')
    return result


def policy():
    from applypilot.country_context import current
    context=current()
    return context.config.get('_run_scope') if context else None


def run_connection():
    from applypilot.country_context import current
    from applypilot.database import get_connection
    context=current()
    return get_connection(Path(context.workspace)/'applypilot.db') if context and policy() else get_connection()


def normalized_title(value):
    return re.sub(r'[^\w+#]+',' ',str(value or '').casefold()).strip()


def title_matches(title, queries, enabled=True):
    if not enabled:return True
    text=' '+normalized_title(title)+' '
    return any(' '+normalized_title(q['query'] if isinstance(q,dict) else q)+' ' in text for q in queries if normalized_title(q.get('query') if isinstance(q,dict) else q))


def excluded_title(title,phrases):
    text=' '+normalized_title(title)+' '
    return any(' '+normalized_title(p)+' ' in text for p in phrases if normalized_title(p))


def accepts_title(title):
    budget=_budget.get()
    if budget is None:return True
    accepted=title_matches(title,budget['queries'],budget['policy']['title_match']) and not excluded_title(title,budget['exclude'])
    if not accepted:budget['rejected_titles']+=1
    return accepted


def limit_reached():
    budget=_budget.get()
    return budget is not None and len(budget['urls'])>=budget['policy']['max_new_jobs']


def budget_active():return _budget.get() is not None


def board_plan(cfg,app_dir):
    from applypilot.geography import locations,board_scope
    from applypilot.pipeline_store import target_offset
    result=[];sites=cfg.get('sites',[]);remaining=cfg['board_search_budget']
    for index,board in enumerate(sites):
        allowance=max(1,remaining//(len(sites)-index))
        override=(cfg.get('board_overrides') or {}).get(board,{})
        queries=[q for q in cfg['queries'] if (not override.get('tiers') or q.get('tier',1) in override['tiers']) and (not override.get('queries') or q['query'] in override['queries'])]
        places=[p for p in locations(cfg) if (not override.get('location_labels') or p.get('label') in override['location_labels']) and board in board_scope(p,[board])[0]]
        expanded=[{**p,'board':board,'query':q['query'],'tier':q.get('tier',1)} for p in places for q in queries]
        offset=(target_offset(app_dir,[board])+len(result))%max(1,len(expanded))
        chosen=(expanded[offset:]+expanded[:offset])[:allowance]
        result.extend(chosen);remaining-=len(chosen)
        if remaining<=0:break
    return result


def workday_plan(cfg,app_dir):
    from applypilot.source_registry import employers
    from applypilot.pipeline_store import target_offset
    portals=employers(app_dir)
    queries=[q['query'] for q in cfg['queries'] if q.get('tier',1)<=cfg.get('workday_max_tier',3)] or [q['query'] for q in cfg['queries']]
    if not queries or not portals:return []
    budget=cfg['workday_target_budget'];cursor=target_offset(app_dir,['Workday'])
    start=cursor%len(queries);queries=(queries[start:]+queries[:start])[:budget]
    items=list(portals.items());start=(cursor//max(1,len(queries)))%len(items);items=items[start:]+items[:start]
    result=[]
    for index,query in enumerate(queries):
        take=min(len(items),max(1,(budget-len(result))//(len(queries)-index)))
        result.extend({'key':key,'name':item['name'],'query':query} for key,item in items[:take])
        if len(result)>=budget:break
    return result


def direct_plan(cfg,app_dir):
    from applypilot.config import load_sites_config
    from applypilot.discovery.smartextract import build_scrape_targets,_dedupe_site_query_location_twins,_balanced_target_window
    from applypilot.pipeline_store import target_offset
    if cfg.get('smart_sources_disabled'):return []
    targets=build_scrape_targets(load_sites_config(app_dir).get('sites',[]),cfg)
    targets=_dedupe_site_query_location_twins([t for t in targets if t['name'] in cfg.get('smart_sites',[])])
    offset=target_offset(app_dir,{t['name'] for t in targets})
    return _balanced_target_window(targets,cfg['smart_target_budget'],offset=offset,search_cfg=cfg)


def membership_key(run_id):return '__discovery_run__:'+str(run_id)
def collection_key(run_id):return '__discovery_added__:'+str(run_id)


def record_new(conn,url):
    budget=_budget.get()
    if budget is not None:
        conn.execute('INSERT OR IGNORE INTO cc_campaign_jobs(campaign_id,job_url) VALUES(?,?)',(collection_key(budget['run_id']),url))
        if budget['policy']['processing_scope']=='new':
            conn.execute('INSERT OR IGNORE INTO cc_campaign_jobs(campaign_id,job_url) VALUES(?,?)',(membership_key(budget['run_id']),url))
        budget['urls'].add(url)


def storage_batch(function):
    @wraps(function)
    def wrapped(conn,*args,**kwargs):
        budget=_budget.get()
        if budget is None:return function(conn,*args,**kwargs)
        database=Path(conn.execute('PRAGMA database_list').fetchone()[2]).resolve()
        if database!=budget['root']/'applypilot.db':raise ValueError('Discovery storage is outside the captured workspace')
        with budget['lock']:
            budget['urls']={r[0] for r in conn.execute('SELECT job_url FROM cc_campaign_jobs WHERE campaign_id=?',(collection_key(budget['run_id']),))}
            try:return function(conn,*args,**kwargs)
            except Exception:
                conn.rollback()
                budget['urls']={r[0] for r in conn.execute('SELECT job_url FROM cc_campaign_jobs WHERE campaign_id=?',(collection_key(budget['run_id']),))}
                raise
    return wrapped


def bounded_discovery(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        from applypilot.country_context import current
        context=current();settings=policy()
        if settings is None:return function(*args,**kwargs)
        from applypilot.database import get_connection
        run_id=os.environ.get('APPLYPILOT_RUN_ID')
        if not run_id:raise ValueError('Bounded discovery requires a captured run identifier')
        conn=get_connection(Path(context.workspace)/'applypilot.db')
        budget={'policy':validate_policy(settings),'queries':context.config.get('queries',[]),'exclude':context.config.get('exclude_titles',[]),'root':Path(context.workspace).resolve(),
                'run_id':run_id,'lock':threading.RLock(),'urls':{r[0] for r in conn.execute('SELECT job_url FROM cc_campaign_jobs WHERE campaign_id=?',(collection_key(run_id),))},'rejected_titles':0}
        token=_budget.set(budget)
        try:
            result=function(*args,**kwargs)
            result['collection_scope']={'new_jobs':len(budget['urls']),'maximum_new_jobs':settings['max_new_jobs'],'title_rejections':budget['rejected_titles'],'limit_reached':limit_reached()}
            return result
        finally:_budget.reset(token)
    return wrapped


def initialize_selection(conn,run_id,settings):
    if settings and settings['processing_scope']=='matching':
        conn.executemany('INSERT OR IGNORE INTO cc_campaign_jobs(campaign_id,job_url) VALUES(?,?)',[(membership_key(run_id),u) for u in settings.get('saved_job_urls',[])])
        conn.commit()


@contextmanager
def selection_scope(run_id,settings):
    token=_selection.set((run_id,settings))
    try:yield
    finally:_selection.reset(token)


def processing_where():
    selected=_selection.get()
    if selected is None:
        settings=policy();run_id=os.environ.get('APPLYPILOT_RUN_ID')
        if not settings:return None
        if not run_id:raise ValueError('Captured processing scope requires a run identifier')
        selected=(run_id,settings)
    run_id,settings=selected
    if run_id is None:
        urls=settings.get('saved_job_urls',[])
        return 'url IN ('+','.join("'"+u.replace("'","''")+"'" for u in urls)+')' if urls else '0=1'
    key=membership_key(run_id).replace("'","''")
    limit=int(settings['max_new_jobs'])
    return f"url IN (SELECT job_url FROM cc_campaign_jobs WHERE campaign_id='{key}' ORDER BY rowid LIMIT {limit})"


def processing_allows(conn,url):
    where=processing_where()
    return not where or bool(conn.execute('SELECT 1 FROM jobs WHERE url=? AND '+where,(url,)).fetchone())


def renamed_job(conn,old_url,new_url,merged=False):
    if not policy() or not os.environ.get('APPLYPILOT_RUN_ID'):return
    keys=(membership_key(os.environ['APPLYPILOT_RUN_ID']),collection_key(os.environ['APPLYPILOT_RUN_ID']))
    for key in keys:
        if not merged:conn.execute('UPDATE OR IGNORE cc_campaign_jobs SET job_url=? WHERE campaign_id=? AND job_url=?',(new_url,key,old_url))
        conn.execute('DELETE FROM cc_campaign_jobs WHERE campaign_id=? AND job_url=?',(key,old_url))


def effective_config(conn,app_dir,data):
    """One merge for preview and launch, including unsaved editor controls."""
    from applypilot.pipeline_workspace import campaign_config
    from applypilot import setup_workspace as setup
    cfg=campaign_config(app_dir,data.get('campaign_id'))
    from applypilot.country_context import current,CountryError
    if current() and cfg.get('country_code') and current().country_code and cfg['country_code']!=current().country_code:
        raise CountryError('Choose the campaign in its own country workspace')
    patch=data.get('config_patch') or {}
    allowed={'queries','locations','boards','smart_sites','smart_sources_disabled','defaults','country','geography_enabled','location_filter_mode','exclude_titles','resume_routing','workday_max_tier','ai_prices','discovery_defaults'}
    if not isinstance(patch,dict) or set(patch)-allowed:raise ValueError('Unsupported draft search setting')
    cfg.update(patch)
    if data.get('search_queries'):
        cfg['queries']=[{'query':q,'tier':next((v.get('tier',1) for v in cfg.get('queries',[]) if v['query']==q),1)} for q in data['search_queries']]
    if 'boards' in patch:cfg['sites']=list(cfg['boards'])
    if data.get('scope_policy') is None:return cfg
    for key in ('discover_skip_jobspy','discover_skip_workday','discover_skip_smarte','selected_only','stream'):
        if key in data and not isinstance(data[key],bool):raise ValueError('Run source and processing switches must be true/false')
    settings=validate_policy(data['scope_policy'])
    stages=data.get('stages') or ['discover','enrich','score']
    settings['explicit_picks']=bool(data.get('selected_only')) and set(stages).issubset({'tailor','statement','cover','pdf'})
    if settings['processing_scope']=='new' and 'discover' not in stages:raise ValueError('New-job scope needs discovery; choose matching saved jobs for existing work')
    if not cfg.get('queries') or not cfg.get('locations'):raise ValueError('Choose at least one role and location')
    for key in ('board_search_budget','smart_target_budget','workday_target_budget'):
        number=data.get(key,6)
        if isinstance(number,bool) or not isinstance(number,int) or not 1<=number<=100:raise ValueError('Scoped source budgets must be 1–100; zero does not mean all')
        cfg[key]=number
    cfg['defaults']=dict(cfg.get('defaults') or {},results_per_site=settings['results_per_search'])
    cfg['board_overrides']={k:dict(v,results_per_site=min(settings['results_per_search'],int(v.get('results_per_site') or settings['results_per_search']))) for k,v in (cfg.get('board_overrides') or {}).items()}
    selected_boards=list(cfg.get('boards',cfg.get('sites',[])) or [])
    cfg['_skipped_boards']=[b for b in selected_boards if b=='google']
    cfg['boards']=cfg['sites']=[b for b in selected_boards if b!='google']
    cfg['smart_sources_disabled']=bool(cfg.get('smart_sources_disabled')) or not bool(cfg.get('smart_sites'))
    setup._validate_searches_dict(cfg,app_dir)
    settings['saved_job_urls']=[]
    if settings['processing_scope']=='matching':
        from applypilot.eligibility import candidates
        from applypilot.discovery.jobspy import _load_location_config,_location_ok
        matcher,reject=_load_location_config(cfg)
        from applypilot.geography import SelectedLocations
        from applypilot.country_context import in_operation_queue
        preliminary=[]
        for row in conn.execute('SELECT url,title,location,country_code,campaign_id FROM jobs ORDER BY discovered_at DESC'):
            job=dict(row)
            if not in_operation_queue(job):continue
            place_ok=matcher.matches(job.get('location')) if isinstance(matcher,SelectedLocations) else _location_ok(job.get('location') or '',matcher,reject)
            if settings['explicit_picks'] or place_ok and title_matches(job.get('title'),cfg['queries'],settings['title_match']) and not excluded_title(job.get('title'),cfg.get('exclude_titles',[])):preliminary.append(job['url'])
        seen=set()
        with selection_scope(None,{'saved_job_urls':preliminary}):
            for stage in stages:
                if stage=='discover':continue
                for job in candidates(conn,stage,int(data.get('min_score',7)),picked=bool(data.get('selected_only')),app_dir=app_dir):
                    if job['url'] in seen:continue
                    if data.get('campaign_id') and job.get('campaign_id')!=data['campaign_id'] and not conn.execute('SELECT 1 FROM cc_campaign_jobs WHERE campaign_id=? AND job_url=?',(data['campaign_id'],job['url'])).fetchone():continue
                    seen.add(job['url']);settings['saved_job_urls'].append(job['url'])
                    if len(seen)>=settings['max_new_jobs']:break
                if len(seen)>=settings['max_new_jobs']:break
    cfg['_run_scope']=settings
    if 'discover' in stages:
        cfg['_planned_board_searches']=[] if data.get('discover_skip_jobspy') else board_plan(cfg,app_dir)
        cfg['_planned_workday_searches']=[] if data.get('discover_skip_workday') else workday_plan(cfg,app_dir)
        cfg['_planned_direct_targets']=[] if data.get('discover_skip_smarte') else direct_plan(cfg,app_dir)
    return cfg


def preview_token(cfg,data,revision):
    from applypilot.canonical import digest
    keys=('stages','min_score','workers','stream','selected_only','discover_skip_jobspy','discover_skip_workday','discover_skip_smarte','campaign_id')
    return digest([cfg,{k:data.get(k) for k in keys},revision])
