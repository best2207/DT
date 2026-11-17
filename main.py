from __future__ import annotations
import argparse, hashlib, hmac, html, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
import requests
import feedparser

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CFG = ROOT / "config"
UA = "TradeRiskMonitor/1.0 (+GitHub Actions public-source monitor)"

TITLE_RULES = [
    (re.compile(r"^Corrugated Die-Cut Cardboard Boxes From (.+?); Determinations$", re.I), "瓦楞模切纸箱（涉及{0}）：美国调查裁定"),
    (re.compile(r"^Hardwood and Decorative Plywood From (.+?): Countervailing Duty Orders$", re.I), "硬木及装饰胶合板（涉及{0}）：反补贴税令"),
    (re.compile(r"^Hardwood and Decorative Plywood From (.+?): Antidumping Duty Orders$", re.I), "硬木及装饰胶合板（涉及{0}）：反倾销税令"),
    (re.compile(r"^Wood Mouldings and Millwork Products From (.+?): Final Results of Antidumping Duty Administrative Review; (.+)$", re.I), "木制线条及木工制品（涉及{0}）：反倾销行政复审最终结果（{1}）"),
    (re.compile(r"^Certain Metal Lockers and Parts Thereof From (.+?): (.+)$", re.I), "金属储物柜及零部件（涉及{0}）：反补贴行政复审进展"),
    (re.compile(r"^Wooden Cabinets and Vanities and Components Thereof From (.+?): (.+)$", re.I), "木制橱柜、浴室柜及零部件（涉及{0}）：反补贴行政复审进展"),
    (re.compile(r"^Certain Pea Protein From (.+?): (.+)$", re.I), "豌豆蛋白（涉及{0}）：反倾销行政复审进展"),
    (re.compile(r"^Diamond Sawblades and Parts Thereof From (.+?): (.+)$", re.I), "金刚石锯片及零部件（涉及{0}）：反倾销税令延续"),
]
COUNTRY_TRANSLATIONS = {
    "the People's Republic of China": "中国", "People's Republic of China": "中国",
    "the Socialist Republic of Vietnam": "越南", "Socialist Republic of Vietnam": "越南",
    "the Republic of Türkiye": "土耳其", "Republic of Türkiye": "土耳其",
    "Taiwan": "中国台湾", "Malaysia": "马来西亚", "Indonesia": "印度尼西亚",
    "Mexico": "墨西哥", "India": "印度", "Spain": "西班牙", "Austria": "奥地利",
    "Thailand": "泰国", "China": "中国", "United States": "美国", "the United States": "美国"
}

def load(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return default

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def now(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or now()).isoformat(timespec="seconds")
def key(text): return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", (text or "").lower())
def event_id(url, title): return hashlib.sha256(((url or "") + title).encode("utf-8")).hexdigest()[:20]

def translate_fragment(text):
    result=text
    for source, target in sorted(COUNTRY_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        result=result.replace(source,target)
    return re.sub(r"\s+", " ", result).strip(" ;:")

def summarize_event(event):
    title=html.unescape(event.get("title","")).strip()
    for pattern, template in TITLE_RULES:
        match=pattern.match(title)
        if match:
            translated=[translate_fragment(group) for group in match.groups()]
            chinese=template.format(*translated)
            return chinese, chinese
    translated=translate_fragment(title)
    summary=html.unescape(re.sub(r"<[^>]+>"," ",event.get("snippet","")))
    summary=re.sub(r"\s+"," ",summary).strip()
    if summary and not re.search(r"[\u3400-\u9fff]",summary):
        summary="来源摘要（原文）："+summary[:220]
    elif summary:
        summary=summary[:240]
    else:
        summary="公开来源发布了相关政策/贸易救济进展；请核对原文中的涉案产品、税率及生效日期。"
    return translated or title, summary



def request_json(url, params=None, timeout=25):
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()

def fetch_federal(cfg, hours):
    out=[]; since=(now()-timedelta(hours=hours)).date().isoformat()
    for term in cfg.get("terms", []):
        try:
            data=request_json("https://www.federalregister.gov/api/v1/documents.json", {
                "per_page": min(int(cfg.get("per_page",50)),100), "order":"newest", "conditions[term]":term,
                "conditions[publication_date][gte]":since
            })
            for x in data.get("results",[]):
                out.append({"title":x.get("title",""),"url":x.get("html_url") or x.get("pdf_url",""),
                    "published":x.get("publication_date",""),"source":"Federal Register","country":"US",
                    "snippet":x.get("abstract") or "", "type":"official_policy"})
        except Exception as e: print(f"Federal Register term={term!r}: {e}",file=sys.stderr)
    return out

def fetch_gdelt(cfg):
    out=[]
    for q in cfg.get("queries",[]):
        try:
            data=request_json("https://api.gdeltproject.org/api/v2/doc/doc",{
                "query":q,"mode":"artlist","format":"json","maxrecords":min(int(cfg.get("max_records",20)),50),"timespan":"24h","sort":"DateDesc"
            })
            for x in data.get("articles",[]):
                out.append({"title":x.get("title",""),"url":x.get("url",""),"published":x.get("seendate",""),
                    "source":x.get("domain") or "GDELT","country":x.get("sourcecountry",""),"snippet":"","type":"news"})
            time.sleep(5)
        except Exception as e: print(f"GDELT query={q!r}: {e}",file=sys.stderr); time.sleep(5)
    return out

class LinkParser:
    """Small HTML parser for public listing pages; O(n) over downloaded markup."""
    from html.parser import HTMLParser
    class Parser(HTMLParser):
        def __init__(self): super().__init__(); self.links=[]; self.href=None; self.buf=[]
        def handle_starttag(self, tag, attrs):
            if tag.lower()=="a": self.href=dict(attrs).get("href"); self.buf=[]
        def handle_data(self,data):
            if self.href is not None: self.buf.append(data.strip())
        def handle_endtag(self,tag):
            if tag.lower()=="a" and self.href is not None:
                title=" ".join(x for x in self.buf if x)
                if title and len(title)>5: self.links.append((title,self.href))
                self.href=None; self.buf=[]

def fetch_cacs(cfg):
    url=cfg.get("url","https://cacs.mofcom.gov.cn/cacscms/view/searchList")
    try:
        r=requests.get(url,timeout=25,headers={"User-Agent":UA}); r.raise_for_status()
        parser=LinkParser.Parser(); parser.feed(r.text)
        from urllib.parse import urljoin
        items=[]
        for title,href in parser.links[:120]:
            if any(w in title.lower() for w in ("反倾销","反补贴","贸易","调查","关税","措施","china","中国","公告")):
                items.append({"title":title,"url":urljoin(url,href),"published":"","source":"中国贸易救济信息网",
                    "country":"CN","snippet":"","type":"official_policy"})
        return items
    except Exception as e: print(f"MOFCOM CACS: {e}",file=sys.stderr); return []

def fetch_rss(cfg):
    out=[]
    for url in cfg.get("feeds",[]):
        try:
            feed=feedparser.parse(url)
            for x in feed.entries[:100]:
                out.append({"title":x.get("title",""),"url":x.get("link",""),"published":x.get("published",""),
                    "source":feed.feed.get("title") or url,"country":"","snippet":x.get("summary",""),"type":"news"})
        except Exception as e: print(f"RSS {url}: {e}",file=sys.stderr)
    return out

def collect(sources, hours):
    all_items=[]; health={}
    adapters={"federal_register":fetch_federal,"gdelt":fetch_gdelt,"mofcom_cacs":fetch_cacs,"rss":fetch_rss}
    for name,cfg in sources.items():
        if not cfg.get("enabled",False): continue
        started=time.monotonic()
        try:
            found=adapters[name](cfg,hours) if name=="federal_register" else adapters[name](cfg)
            all_items.extend(found); health[name]={"ok":True,"count":len(found),"checked_at":iso(),"seconds":round(time.monotonic()-started,2)}
        except Exception as e: health[name]={"ok":False,"error":str(e),"checked_at":iso()}
    return all_items,health

def annotate(item, companies, settings):
    text=" ".join(str(item.get(k,"")) for k in ("title","snippet","source"))
    norm=key(text); matches=[]
    for c in companies:
        names=[c.get("name","")]+c.get("aliases",[])
        hits=[n for n in names if n and key(n) and key(n) in norm]
        if hits: matches.append({"name":c.get("name"),"matched_aliases":hits,"industry":c.get("industry","")})
    ch=any(key(w) in norm for w in settings.get("china_keywords",[]))
    level="P3"
    for level_name in ("P1","P2"):
        if any(key(w) in norm for w in settings.get("risk_keywords",{}).get(level_name,[])):
            level=level_name; break
    if ch and level=="P3": level="P2"
    if matches and level=="P3": level="P2"
    item.update({"id":event_id(item.get("url",""),item.get("title","")),"china_related":ch,"risk_level":level,"matched_companies":matches,"fetched_at":iso()})
    return item

def merge(items, previous, companies, settings):
    known={x.get("id") for x in previous}; byid={x.get("id"):x for x in previous}
    fresh=[]
    for raw in items:
        if not raw.get("title") or not raw.get("url"): continue
        x=annotate(raw,companies,settings)
        if x["id"] not in byid: fresh.append(x)
        byid[x["id"]]=x
    ordered=sorted(byid.values(),key=lambda x:(x.get("published","") or x.get("fetched_at","")),reverse=True)
    return ordered[:int(settings.get("max_events",3000))],fresh

def render_event(event):
    title, summary=summarize_event(event)
    companies=", ".join(x["name"] for x in event.get("matched_companies",[])) or "未匹配监控企业"
    scope="涉华" if event.get("china_related") else "国际政策"
    date=event.get("published","")[:10] or "日期未知"
    return (f"[{event.get('risk_level','P3')}] {scope}｜{title}\n"
            f"简述：{summary}\n企业：{companies}｜日期：{date}\n"
            f"来源：{event.get('source','未知来源')}｜{event.get('url','')}")

def feishu_send(events, settings):
    webhook=os.getenv("FEISHU_WEBHOOK_URL","").strip()
    if not webhook: return {"sent":0,"sent_ids":[],"messages":0,"status":"FEISHU_WEBHOOK_URL not configured"}
    pending=[e for e in events if e.get("risk_level") in ("P1","P2") and not e.get("feishu_sent_at")]
    if not pending: return {"sent":0,"sent_ids":[],"messages":0,"status":"no pending P1/P2 events"}
    secret=os.getenv("FEISHU_SIGNING_SECRET","").strip()
    max_chars=int(settings.get("feishu_message_max_chars",12000))
    header=f"全球外贸风险监控｜待处理 {len(pending)} 条"
    batches=[]; blocks=[header]; batch_ids=[]
    for event in pending:
        block=render_event(event)
        candidate="\n\n".join(blocks+[block])
        if len(candidate)>max_chars and len(blocks)>1:
            batches.append(("\n\n".join(blocks),batch_ids))
            blocks=[header+"（续）"]; batch_ids=[]
        blocks.append(block); batch_ids.append(event.get("id",""))
    if len(blocks)>1: batches.append(("\n\n".join(blocks),batch_ids))
    sent_ids=[]; errors=[]
    for text,batch in batches:
        body={"msg_type":"text","content":{"text":text}}
        if secret:
            ts=str(int(time.time()))
            body["timestamp"]=ts
            body["sign"]=base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(),b"",digestmod=hashlib.sha256).digest()).decode()
        try:
            response=requests.post(webhook,json=body,timeout=20); response.raise_for_status()
            result=response.json()
            if result.get("code",0)!=0: raise RuntimeError(str(result))
            sent_ids.extend(batch)
        except Exception as exc:
            errors.append(str(exc)[:300])
            break
    return {"sent":len(sent_ids),"sent_ids":sent_ids,"messages":len(batches),"errors":errors[:3],
            "status":"ok" if not errors else "partial"}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--once",action="store_true"); ap.add_argument("--offline",action="store_true"); args=ap.parse_args()
    settings=load(CFG/"settings.json",{}); companies=load(CFG/"companies.json",{}).get("companies",[])
    sources=load(CFG/"sources.json",{}); previous=load(DATA/"events.json",[])
    if args.offline:
        inputs=[{"title":"EU opens anti-dumping investigation on Chinese steel imports","url":"https://example.org/test/steel","published":iso(),"source":"fixture","country":"EU","snippet":"China-origin goods and tariff review","type":"official_policy"},
                {"title":"Company Alpha faces insolvency lawsuit","url":"https://example.org/test/alpha","published":iso(),"source":"fixture","country":"US","snippet":"","type":"news"}]
        if not companies: companies=[{"name":"Company Alpha","aliases":["Alpha Corp"]}]
        health={"offline_fixture":{"ok":True,"count":len(inputs),"checked_at":iso()}}
    else: inputs,health=collect(sources,int(settings.get("poll_hours",24)))
    merged,fresh=merge(inputs,previous,companies,settings)
    alerts=[x for x in merged if x.get("risk_level") in ("P1","P2") and not x.get("feishu_sent_at")]
    sent={"sent":0,"sent_ids":[],"status":"notification disabled"} if args.no_notify else feishu_send(alerts,settings)
    sent_ids=set(sent.get("sent_ids",[]))
    for event in merged:
        if event.get("id") in sent_ids: event["feishu_sent_at"]=iso()
    save(DATA/"events.json",merged)
    state={"updated_at":iso(),"sources":health,"companies_monitored":len(companies),"events_total":len(merged),"new_events":len(fresh),"new_alerts":sum(1 for x in fresh if x["risk_level"] in ("P1","P2")),"pending_alerts":sum(1 for x in alerts if x.get("id") not in sent_ids),"feishu":{k:v for k,v in sent.items() if k!="sent_ids"}}
    save(DATA/"state.json",state)
    print(json.dumps(state,ensure_ascii=False,indent=2))

if __name__=="__main__": main()

