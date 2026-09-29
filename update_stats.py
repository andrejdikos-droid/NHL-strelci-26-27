#!/usr/bin/env python3
import json, unicodedata, re, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
LEAGUE=ROOT/'data/league.json'; STATS=ROOT/'data/stats.json'; HISTORY=ROOT/'data/history.json'
def load(path,fallback):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except Exception:return fallback
def norm(s):
    s=unicodedata.normalize('NFKD',s or '');s=''.join(c for c in s if not unicodedata.combining(c));s=s.lower().replace('’',"'").replace('.','');return re.sub(r'[^a-z0-9]+',' ',s).strip()
def fetch_stats(season,game_type):
    base='https://api.nhle.com/stats/rest/en/skater/summary'
    params={'isAggregate':'false','isGame':'false','start':'0','limit':'-1','sort':json.dumps([{'property':'goals','direction':'DESC'}],separators=(',',':')),'cayenneExp':f'seasonId={season} and gameTypeId={game_type}'}
    req=urllib.request.Request(base+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'NHL-Strelci-26-27/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r).get('data',[])
def main():
    league=load(LEAGUE,{});prev=load(STATS,{'players':{}});history=load(HISTORY,{'snapshots':[]});season=league['season'];game_type=league.get('gameTypeId',2)
    rows=fetch_stats(season,game_type);by_name={norm(x.get('skaterFullName')):x for x in rows};by_last={}
    for x in rows:
        full=x.get('skaterFullName','');key=norm(x.get('lastName') or (full.split()[-1] if full else ''));by_last.setdefault(key,[]).append(x)
    roster=[]
    for m in league['managers']:
        for slot in m['roster']:
            if slot['player'] not in roster:roster.append(slot['player'])
    players={};unmatched=[]
    for name in roster:
        match=by_name.get(norm(name))
        if not match:
            last=norm(name).split()[-1] if norm(name) else '';cands=by_last.get(last,[])
            if len(cands)==1:match=cands[0]
        old=prev.get('players',{}).get(name,{});oldg=int(old.get('goals',0) or 0)
        if match:
            g=int(match.get('goals',0) or 0);players[name]={'playerId':match.get('playerId'),'goals':g,'gamesPlayed':int(match.get('gamesPlayed',0) or 0),'team':match.get('teamAbbrevs') or '','shots':int(match.get('shots',0) or 0),'shootingPct':match.get('shootingPct'),'deltaGoals':max(0,g-oldg),'found':True}
        else:
            players[name]={'playerId':old.get('playerId'),'goals':oldg,'gamesPlayed':int(old.get('gamesPlayed',0) or 0),'team':old.get('team',''),'shots':old.get('shots',0),'shootingPct':old.get('shootingPct'),'deltaGoals':0,'found':False};unmatched.append(name)
    now=datetime.now(timezone.utc).isoformat();out={'season':season,'gameTypeId':game_type,'updatedAt':now,'source':'NHL Stats API','players':players,'unmatchedPlayers':unmatched,'message':'OK' if rows else 'NHL API zatiaľ nevrátilo regular-season dáta.'};STATS.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    n=len(league['managers']);value=league.get('valuePerGoal',1);totals={}
    for m in league['managers']:
        g=0
        for slot in m['roster']:
            current=players.get(slot['player'],{}).get('goals',0);g+=int(slot.get('bankedGoals',0))+max(0,current-int(slot.get('goalsAtAcquisition',0)))
        totals[m['name']]=g
    league_total=sum(totals.values());snap={'date':datetime.now(timezone.utc).date().isoformat(),'updatedAt':now,'managers':{name:{'goals':g,'net':(n*g-league_total)*value} for name,g in totals.items()}}
    snaps=history.setdefault('snapshots',[])
    if snaps and snaps[-1].get('date')==snap['date']:snaps[-1]=snap
    else:snaps.append(snap)
    HISTORY.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8');print(f'Updated {len(players)} drafted players; NHL rows={len(rows)}; unmatched={len(unmatched)}')
if __name__=='__main__':main()
