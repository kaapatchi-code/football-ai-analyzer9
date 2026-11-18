import os, math, json, time, threading, re
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
ALLOWED_ORIGINS = [x.strip() for x in os.getenv('CORS_ORIGINS', '*').split(',') if x.strip()]

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if '*' in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = '*'
    elif origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/api/<path:_path>', methods=['OPTIONS'])
def api_options(_path):
    return ('', 204)

API_KEY = os.getenv('HIGHLIGHTLY_API_KEY', '').strip()
API_HOST = os.getenv('HIGHLIGHTLY_BASE_URL', 'https://soccer.highlightly.net').rstrip('/')
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_DAILY_API_CALLS = 0
_DAILY_USAGE_DATE = datetime.now(timezone.utc).date()
_DAILY_API_SOFT_LIMIT = int(os.getenv('API_DAILY_SOFT_LIMIT', '85'))
_LAST_REMAINING = None


def cache_ttl(path):
    if path.startswith('/teams?'): return 24 * 60 * 60
    if path.startswith('/last-five-games'): return 60 * 60
    if path.startswith('/matches/'): return 24 * 60 * 60
    if path.startswith('/statistics/'): return 24 * 60 * 60
    return 10 * 60


def api_get(path, params=None):
    global _DAILY_USAGE_DATE, _DAILY_API_CALLS, _LAST_REMAINING
    if not API_KEY:
        raise RuntimeError('مفتاح Highlightly غير مضبوط. أضف HIGHLIGHTLY_API_KEY في إعدادات Render.')
    params = params or {}
    cache_key = path + '?' + json.dumps(params, sort_keys=True, ensure_ascii=False)
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached['time'] < cache_ttl(path):
            return cached['data']
        today = datetime.now(timezone.utc).date()
        if today != _DAILY_USAGE_DATE:
            _DAILY_USAGE_DATE = today
            _DAILY_API_CALLS = 0
            _LAST_REMAINING = None
        if _DAILY_API_CALLS >= _DAILY_API_SOFT_LIMIT:
            raise RuntimeError(f'وصل الموقع إلى حد الحماية اليومي ({_DAILY_API_SOFT_LIMIT} طلباً). انتظر حتى تتجدد حصة Highlightly.')
        _DAILY_API_CALLS += 1
    try:
        response = requests.get(API_HOST + path,
            headers={'x-rapidapi-key': API_KEY}, params=params, timeout=20)
        remaining = response.headers.get('x-ratelimit-requests-remaining')
        if remaining is not None:
            try: _LAST_REMAINING = int(remaining)
            except ValueError: pass
        if response.status_code == 429:
            raise RuntimeError('Highlightly رفض الطلب بسبب بلوغ حد الاستخدام. تحقق من الحصة اليومية في لوحة التحكم.')
        if response.status_code in (401, 403):
            raise RuntimeError('Highlightly رفض المفتاح أو صلاحية الحساب. تحقق من المفتاح والخطة في لوحة Highlightly.')
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError('تعذر الاتصال بـ Highlightly: ' + str(exc)) from exc
    if isinstance(payload, dict) and payload.get('error'):
        raise RuntimeError(str(payload['error']))
    with _CACHE_LOCK:
        _CACHE[cache_key] = {'time': now, 'data': payload}
    return payload


def payload_data(payload):
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        return payload['data']
    if isinstance(payload, list):
        return payload
    return []


def first_record(payload):
    if isinstance(payload, list): return payload[0] if payload else {}
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        return payload['data'][0] if payload['data'] else {}
    return payload if isinstance(payload, dict) else {}


def safe_mean(values):
    return round(sum(values)/len(values), 2) if values else None


def parse_score(score):
    if not score: return (None, None)
    match = re.search(r'(\d+)\s*[-:]\s*(\d+)', str(score))
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def parse_minute(value):
    if value is None: return None
    match = re.match(r'\s*(\d+)', str(value))
    return int(match.group(1)) if match else None


def get_team_last_five(team_id):
    return payload_data(api_get('/last-five-games', {'teamId': int(team_id)}))[:5]


def match_details(match_id):
    return first_record(api_get(f'/matches/{int(match_id)}'))


def stats_for_fixtures(fixtures):
    """Normalize Highlightly's last-five response and detailed match statistics."""
    out = []
    for item in fixtures:
        home = item.get('homeTeam') or {}
        away = item.get('awayTeam') or {}
        state = item.get('state') or {}
        score = state.get('score') or {}
        hg, ag = parse_score(score.get('current'))
        fx = {
            'fixture_id': item.get('id'), 'date': item.get('date'),
            'timestamp': None, 'home_id': home.get('id'), 'away_id': away.get('id'),
            'home_name': home.get('name'), 'away_name': away.get('name'),
            'home_goals': hg, 'away_goals': ag, 'ht_home': None, 'ht_away': None,
            'ft_home': hg, 'ft_away': ag,
            'status': 'FT' if str(state.get('description','')).lower().startswith('finished') else state.get('description','')
        }
        # Detailed match response supplies team statistics and goal events. Missing coverage stays unavailable.
        if fx['fixture_id']:
            try:
                detail = match_details(fx['fixture_id'])
                for event in detail.get('events') or []:
                    minute = parse_minute(event.get('time'))
                    if minute is None or minute > 45: continue
                    event_type = str(event.get('type') or '').lower()
                    if event_type not in ('goal', 'own goal', 'penalty'): continue
                    tid = (event.get('team') or {}).get('id')
                    if tid == fx['home_id']: fx['ht_home'] = (fx['ht_home'] or 0) + 1
                    elif tid == fx['away_id']: fx['ht_away'] = (fx['ht_away'] or 0) + 1
                # If the match had no first-half goals, an available event list means 0-0 at half-time.
                if isinstance(detail.get('events'), list):
                    fx['ht_home'] = fx['ht_home'] or 0
                    fx['ht_away'] = fx['ht_away'] or 0
                fx['_statistics'] = detail.get('statistics') or []
            except Exception as exc:
                fx['_statistics'] = []
                fx['_detail_note'] = str(exc)[:100]
        out.append(fx)
    return out


def team_metrics(team_id, fixtures):
    played = [x for x in fixtures if str(x['status']).lower().startswith('ft') and x['home_goals'] is not None and x['away_goals'] is not None]
    gf=[]; ga=[]; outcomes=[]; btts=[]; o15=[]; o25=[]; o35=[]; clean=[]; fts=[]; ht_g=[]; ht_con=[]; st_g=[]; st_con=[]; comebacks=[]
    home=[]; away=[]
    for x in played:
        is_home = int(x['home_id']) == int(team_id)
        scored = x['home_goals'] if is_home else x['away_goals']
        conceded = x['away_goals'] if is_home else x['home_goals']
        gf.append(scored); ga.append(conceded)
        outcomes.append('W' if scored>conceded else 'D' if scored==conceded else 'L')
        btts.append(int(scored>0 and conceded>0)); total=scored+conceded
        o15.append(int(total>=2)); o25.append(int(total>=3)); o35.append(int(total>=4))
        clean.append(int(conceded==0)); fts.append(int(scored==0))
        if x['ht_home'] is not None and x['ht_away'] is not None:
            hts = x['ht_home'] if is_home else x['ht_away']; htc = x['ht_away'] if is_home else x['ht_home']
            ht_g.append(hts); ht_con.append(htc)
            st_g.append(max(0,scored-hts)); st_con.append(max(0,conceded-htc))
            comebacks.append(1 if hts < htc and scored > conceded else 0)
        if is_home: home.append((scored,conceded))
        else: away.append((scored,conceded))
    return {
        'played':len(played),'form':outcomes,'wins':outcomes.count('W'),'draws':outcomes.count('D'),'losses':outcomes.count('L'),
        'gf':gf,'ga':ga,'avg_gf':safe_mean(gf),'avg_ga':safe_mean(ga),
        'btts_pct':round(100*sum(btts)/len(btts),1) if btts else None,
        'over15_pct':round(100*sum(o15)/len(o15),1) if o15 else None,
        'over25_pct':round(100*sum(o25)/len(o25),1) if o25 else None,
        'over35_pct':round(100*sum(o35)/len(o35),1) if o35 else None,
        'clean_pct':round(100*sum(clean)/len(clean),1) if clean else None,
        'fts_pct':round(100*sum(fts)/len(fts),1) if fts else None,
        'ht_avg_gf':safe_mean(ht_g),'ht_avg_ga':safe_mean(ht_con),
        'st_avg_gf':safe_mean(st_g),'st_avg_ga':safe_mean(st_con),
        'comebacks':sum(comebacks) if comebacks else (0 if played and len(ht_g)==len(played) else None),
        'home_avg_gf':safe_mean([a for a,b in home]),'home_avg_ga':safe_mean([b for a,b in home]),
        'away_avg_gf':safe_mean([a for a,b in away]),'away_avg_ga':safe_mean([b for a,b in away]),
        'last_fixtures':played[:5]
    }


def team_extras(team_id, fixtures):
    corners=[]; yellow=[]; red=[]; xg=[]; notes=[]
    for fx in fixtures:
        for block in fx.get('_statistics', []):
            if (block.get('team') or {}).get('id') != int(team_id): continue
            for stat in block.get('statistics') or []:
                name = str(stat.get('displayName') or '').strip().lower()
                value = stat.get('value')
                try: value = float(value)
                except (ValueError, TypeError): continue
                if 'corner' in name: corners.append(value)
                elif 'yellow' in name: yellow.append(value)
                elif 'red' in name: red.append(value)
                elif name in ('xg', 'expected goals', 'expected goals (xg)') or 'expected goals' in name: xg.append(value)
    if not any(fx.get('_statistics') for fx in fixtures):
        notes.append('إحصائيات المباريات التفصيلية غير متاحة لبعض أو كل المباريات حسب تغطية الدوري.')
    notes.append('قائمة الإصابات وبيانات odds ليست ضمن البيانات التي جرى ربطها في هذه النسخة.')
    return {'avg_corners':safe_mean(corners),'avg_yellow':safe_mean(yellow),'avg_red':safe_mean(red),'avg_xg':safe_mean(xg),
            'injuries':[],'extras_notes':notes,'stats_sample':max(len(corners),len(yellow),len(red),len(xg))}


def poisson(lam, k):
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def model_probs(a, b):
    if a['avg_gf'] is None or a['avg_ga'] is None or b['avg_gf'] is None or b['avg_ga'] is None or min(a['played'],b['played']) < 5:
        return None
    home_lam = max(.15, (a['avg_gf'] + b['avg_ga'])/2 * 1.08)
    away_lam = max(.15, (b['avg_gf'] + a['avg_ga'])/2)
    ph=[poisson(home_lam,i) for i in range(9)]; pa=[poisson(away_lam,i) for i in range(9)]
    win=draw=loss=over25=over35=btts=0
    for i,p1 in enumerate(ph):
        for j,p2 in enumerate(pa):
            p=p1*p2
            if i>j: win+=p
            elif i==j: draw+=p
            else: loss+=p
            if i+j>=3: over25+=p
            if i+j>=4: over35+=p
            if i>0 and j>0: btts+=p
    return {'home_lambda':round(home_lam,2),'away_lambda':round(away_lam,2),'home_win':round(win*100,1),
            'draw':round(draw*100,1),'away_win':round(loss*100,1),'over25':round(over25*100,1),
            'over35':round(over35*100,1),'btts':round(btts*100,1),
            'model':'Poisson مبسط من آخر 5 نتائج مكتملة ومتوسط الأهداف؛ ليس xG ولا ضماناً.'}


@app.route('/')
def home():
    return render_template('index.html')


@app.get('/api/status')
def status():
    return jsonify({'api_configured':bool(API_KEY),'provider':'Highlightly','mode':'live' if API_KEY else 'manual',
                    'server_daily_calls':_DAILY_API_CALLS,'server_daily_soft_limit':_DAILY_API_SOFT_LIMIT,
                    'provider_remaining':_LAST_REMAINING})


@app.get('/api/search-team')
def search_team():
    q=request.args.get('q','').strip()
    if len(q)<2: return jsonify({'results':[]})
    try:
        res=payload_data(api_get('/teams', {'name':q,'limit':12,'offset':0}))[:12]
        return jsonify({'results':[{'id':x.get('id'),'name':x.get('name'),'country':'','logo':x.get('logo')} for x in res if x.get('id') is not None]})
    except Exception as e:
        return jsonify({'error':str(e)}),400


@app.get('/api/analyze')
def analyze():
    try:
        a_id=int(request.args['home_id']); b_id=int(request.args['away_id'])
        if a_id == b_id: return jsonify({'error':'اختر فريقين مختلفين.'}),400
        a_raw=get_team_last_five(a_id); b_raw=get_team_last_five(b_id)
        a_fx=stats_for_fixtures(a_raw); b_fx=stats_for_fixtures(b_raw)
        a=team_metrics(a_id,a_fx); b=team_metrics(b_id,b_fx)
        a.update(team_extras(a_id,a_fx)); b.update(team_extras(b_id,b_fx))
        model=model_probs(a,b)
        warnings=[]
        if min(a['played'],b['played'])<5: warnings.append('لم تتوفر 5 مباريات مكتملة لكل فريق؛ نموذج الاحتمالات غير متاح أو محدود.')
        if not a['played'] or not b['played']: warnings.append('لا توجد نتائج مكتملة كافية لأحد الفريقين ضمن تغطية المزود.')
        if any(x['ht_home'] is None for x in a_fx+b_fx): warnings.append('إحصائيات الشوط الأول قد تكون ناقصة إذا لم يوفر المزود أحداث المباراة.')
        return jsonify({'team_a':a,'team_b':b,'model':model,'warnings':warnings,
                        'data_note':'المصدر Highlightly. تم جلب آخر خمس مباريات مكتملة فقط لكل فريق. إحصائيات الركنيات والبطاقات وxG تعتمد على تغطية تفاصيل كل مباراة؛ الإصابات وodds غير مفعّلة في هذه النسخة. لا يتم تخمين البيانات الناقصة.',
                        'generated_at':datetime.now(timezone.utc).isoformat()})
    except KeyError:
        return jsonify({'error':'اختر الفريقين من نتائج البحث أولاً.'}),400
    except Exception as e:
        return jsonify({'error':str(e)}),400


if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=False)
