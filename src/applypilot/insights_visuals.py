"""Unique-record analytical cohorts and explicitly timed activity charts."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

RESPONSES={'acknowledged','assessment','interview','offer','rejected'}
MILESTONES={'enriched':'detail_scraped_at','scored':'scored_at','cv':'tailored_at','cover':'cover_letter_at','statement':'supporting_statement_at','submitted':'applied_at'}


def record_scope(records,query,event_urls=()):
    result=[]
    for r in records:
        if query.get('job_url') and r['job_url']!=query['job_url']:
            continue
        if query.get('record_state') and r['record_state']!=query['record_state']:
            continue
        if query.get('site') and (r.get('site') or 'Unknown')!=query['site']:
            continue
        if query.get('hiring_status') and (r.get('hiring_status') or ('submitted' if r.get('applied_at') else 'not_recorded'))!=query['hiring_status']:
            continue
        if query.get('closure_reason') and r.get('closure_reason')!=query['closure_reason']:
            continue
        if query.get('milestone') in MILESTONES and not r.get(MILESTONES[query['milestone']]):
            continue
        if query.get('score_band'):
            score=r.get('fit_score')
            key='unknown' if score is None else 'high' if score>=8 else 'medium' if score>=5 else 'low'
            if key!=query['score_band']:
                continue
        if query.get('search') and r['job_url'] not in event_urls and str(query['search']).casefold() not in ' '.join(str(r.get(k) or '') for k in ('title','company','location','site','notes')).casefold():
            continue
        result.append(r)
    return result


def timestamp(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError):
        return None


def charts(records,events,stages):
    submitted=[r for r in records if r.get('applied_at')]
    responses=[r for r in submitted if r.get('hiring_status') in RESPONSES]
    interviews=[r for r in submitted if r.get('hiring_status') in {'interview','offer'}]
    offers=[r for r in submitted if r.get('hiring_status')=='offer']
    states=Counter(r['record_state'] for r in records)
    outcomes=Counter((r.get('hiring_status') or 'submitted') for r in submitted)
    score_bands=Counter('unknown' if r.get('fit_score') is None else 'high' if r['fit_score']>=8 else 'medium' if r['fit_score']>=5 else 'low' for r in records)
    sources={}
    for r in records:
        source=r.get('site') or 'Unknown'
        bucket=sources.setdefault(source,{'name':source,'opportunities':0,'submitted':0,'responses':0,'interviews':0})
        bucket['opportunities']+=1
        bucket['submitted']+=bool(r.get('applied_at'))
        bucket['responses']+=bool(r.get('applied_at') and r.get('hiring_status') in RESPONSES)
        bucket['interviews']+=bool(r.get('applied_at') and r.get('hiring_status') in {'interview','offer'})
    dates=[timestamp(e.get('occurred_at') or e.get('created_at')) for e in events]
    latest=max([d for d in dates if d],default=datetime.now(timezone.utc))
    start=(latest-timedelta(days=29)).date()
    counts=Counter(d.date().isoformat() for d in dates if d and start<=d.date()<=latest.date())
    days=[{'date':(start+timedelta(days=i)).isoformat(),'events':counts.get((start+timedelta(days=i)).isoformat(),0)} for i in range(30)]
    categories=Counter(e['kind'] for e in events)
    locations=Counter(r.get('location') or 'Not recorded' for r in records)
    closures=Counter(r['closure_reason'] for r in records if r.get('closure_reason'))
    durations=[]
    for r in submitted:
        discovered,applied=timestamp(r.get('discovered_at')),timestamp(r.get('applied_at'))
        if discovered and applied and applied>=discovered:
            durations.append((applied-discovered).total_seconds()/86400)
    return {'denominator':len(records),'record_states':[{'key':k,'label':k.replace('_',' ').title(),'count':v} for k,v in states.items()],
            'milestones':[{**s,'key':list(MILESTONES)[i]} for i,s in enumerate(stages)],
            'hiring_funnel':[{'key':k,'label':label,'count':len(group)} for k,label,group in [('submitted','Submitted',submitted),('responses','Employer responses',responses),('interviews','Interviews / offers',interviews),('offers','Offers',offers)]],
            'response_rate':round(len(responses)*100/len(submitted),1) if submitted else None,
            'outcomes':[{'key':k,'label':k.replace('_',' ').title(),'count':v} for k,v in outcomes.items()],
            'score_bands':[{'key':k,'label':label,'count':score_bands[k]} for k,label in [('high','8–10 · strong fit'),('medium','5–7 · medium fit'),('low','1–4 · lower fit'),('unknown','Unscored / unknown')]],
            'sources':sorted(sources.values(),key=lambda s:(-s['opportunities'],s['name'])),
            'activity_days':days,'activity_categories':[{'key':k,'count':v} for k,v in categories.most_common()],
            'locations':[{'name':k,'count':v} for k,v in locations.most_common(10)],
            'closures':[{'key':k,'label':k.replace('_',' ').title(),'count':v} for k,v in closures.most_common()],
            'median_days_to_submit':round(median(durations),1) if durations else None,'timed_submissions':len(durations),
            'data_quality':{'missing_discovery_dates':sum(not timestamp(r.get('discovered_at')) for r in records),'unscored':score_bands['unknown'],'baseline_observations':sum(e['kind']=='baseline' for e in events),'events':len(events)},
            'explanations':{'milestones':'Each bar counts unique opportunities with a saved milestone date. Documents can be optional; these bars are not a sequential conversion funnel.',
                            'hiring':'Responses count acknowledged, assessment, interview, offer or employer rejection states among submitted applications. Candidate withdrawal and unknown outcomes are not employer responses.',
                            'activity':'Daily bars count saved events by event time (recording time when no separate event date exists). Multiple changes can belong to one opportunity.',
                            'sources':'Source bars count unique opportunity URLs. Small samples describe recorded results, not a ranking of source quality.',
                            'scores':'Scores are saved fit assessments, not predicted hiring probabilities. Unknown scores remain separate.'}}


def usage_charts(conn,prices=None):
    prices=prices or {}
    output=[]
    for table,ledger in [('cc_pipeline_usage','Pipeline'),('cc_ai_usage','Assistant / workspace')]:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
            continue
        for row in conn.execute(f'SELECT provider,model,COUNT(*) requests,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,SUM(CASE WHEN input_tokens IS NOT NULL AND output_tokens IS NOT NULL THEN 1 ELSE 0 END) known_requests FROM {table} GROUP BY provider,model'):
            item=dict(row)
            rate=prices.get(f"{item['provider']}/{item['model']}")
            item['ledger']=ledger
            item['estimated_usd']=((item['input_tokens'] or 0)*float(rate['input_per_million'])+(item['output_tokens'] or 0)*float(rate['output_per_million']))/1e6 if rate and item['known_requests']==item['requests'] else None
            output.append(item)
    return output
