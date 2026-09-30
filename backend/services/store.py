import os,sqlite3,hashlib,re,json,math,time,statistics
from pathlib import Path
DB=Path(os.getenv("GUIDELY_DB","backend/data/guidely.sqlite3"))
SAMPLE=Path("backend/data/sample-docs")
def connect():
 DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def initialize():
 with connect() as c: c.executescript("CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,name TEXT,category TEXT,content TEXT,updated REAL); CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,doc_id TEXT,section TEXT,text TEXT,vector TEXT); CREATE TABLE IF NOT EXISTS queries(id INTEGER PRIMARY KEY,question TEXT,latency REAL,sources TEXT,error TEXT,created REAL); CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY,kind TEXT,message TEXT,created REAL); CREATE TABLE IF NOT EXISTS auth_users(id TEXT PRIMARY KEY,email TEXT UNIQUE,display_name TEXT NOT NULL,role TEXT NOT NULL,created REAL NOT NULL); CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL,FOREIGN KEY(user_id) REFERENCES auth_users(id)); CREATE TABLE IF NOT EXISTS login_codes(email TEXT PRIMARY KEY,code_hash TEXT NOT NULL,expires REAL NOT NULL,sent_at REAL NOT NULL,attempts INTEGER NOT NULL,display_name TEXT NOT NULL); CREATE TABLE IF NOT EXISTS auth_ip_limits(ip TEXT NOT NULL,kind TEXT NOT NULL,window_start REAL NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(ip,kind));")
 if not counts()["documents"]:
  for p in SAMPLE.glob("*.txt"): index_document(p.name,p.read_text(encoding="utf-8"))
def vector(text):
 key=os.getenv("OPENAI_API_KEY")
 if key:
  from openai import OpenAI
  model=os.getenv("OPENAI_EMBEDDING_MODEL","text-embedding-3-small")
  return OpenAI(api_key=key,timeout=12).embeddings.create(model=model,input=text).data[0].embedding
 v=[0.0]*384
 for w in re.findall(r"[a-z0-9]+",text.lower()):
  h=int(hashlib.sha256(w.encode()).hexdigest()[:8],16); v[h%384]+=1 if h&256 else -1
 n=math.sqrt(sum(x*x for x in v)) or 1
 return [x/n for x in v]
def split(text,size=1100):
 out=[]; buf=""
 for p in re.split(r"\n\s*\n",text):
  p=p.strip()
  if not p: continue
  if buf and len(buf)+len(p)>size: out.append(buf); buf=""
  buf=(buf+"\n\n"+p).strip()
 if buf: out.append(buf)
 return out or [text]
def bump(name,n=1):
 with connect() as c: c.execute("INSERT INTO counters VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(name,n))
def log_error(kind,message):
 with connect() as c: c.execute("INSERT INTO errors(kind,message,created) VALUES(?,?,?)",(kind,message[:300],time.time()))
def index_document(name,content,category="General"):
 did=hashlib.sha256(name.encode()).hexdigest()[:16]; pieces=split(content)
 with connect() as c:
  old=c.execute("SELECT content,category FROM documents WHERE id=?",(did,)).fetchone()
  cached=bool(old and old["content"]==content and old["category"]==category)
  if not cached:
   c.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",(did,name,category,content,time.time())); c.execute("DELETE FROM chunks WHERE doc_id=?",(did,))
   for i,t in enumerate(pieces):
    sec=next((x.strip('# ').strip() for x in t.splitlines() if x.startswith('#')),f"Section {i+1}")
    c.execute("INSERT INTO chunks VALUES(?,?,?,?,?)",(f"{did}-{i}",did,sec,t,json.dumps(vector(t))))
 if cached: bump("embedding_cache_hits",len(pieces)); return {"id":did,"name":name,"category":category,"chunks":len(pieces),"cached":True}
 bump("new_embeddings",len(pieces)); return {"id":did,"name":name,"category":category,"chunks":len(pieces)}
def documents():
 with connect() as c: return [dict(r) for r in c.execute("SELECT id,name,category,updated FROM documents ORDER BY name")]
def remove_document(did):
 with connect() as c: c.execute("DELETE FROM chunks WHERE doc_id=?",(did,)); c.execute("DELETE FROM documents WHERE id=?",(did,))
def terms(text):
 stop={"the","a","an","is","are","do","does","i","we","you","our","how","what","to","for","of","in","on","and","or","my","your"}
 result=set()
 for w in re.findall(r"[a-z0-9]+",text.lower()):
  if w.endswith("ies") and len(w)>4: w=w[:-3]+"y"
  elif w.endswith("ing") and len(w)>5: w=w[:-3]
  elif w.endswith("s") and len(w)>4: w=w[:-1]
  if w not in stop: result.add(w)
 return result
def retrieve(q,k=5,category=None):
 qv=vector(q); qt=terms(q)
 with connect() as c: rows=c.execute("SELECT chunks.*,documents.name,documents.category FROM chunks JOIN documents ON chunks.doc_id=documents.id").fetchall()
 scored=[]
 for r in rows:
  if category and category.lower() not in r["category"].lower(): continue
  dt=terms(r["text"]+" "+r["section"]+" "+r["name"]); overlap=len(qt & dt)/max(1,len(qt))
  cosine=sum(a*b for a,b in zip(qv,json.loads(r["vector"])))
  score=max(0,cosine) if os.getenv("OPENAI_API_KEY") else 0.8*overlap+0.2*max(0,cosine)
  scored.append({"id":r["id"],"document":r["name"],"category":r["category"],"section":r["section"],"snippet":r["text"],"score":round(score,4)})
 return sorted(scored,key=lambda x:x["score"],reverse=True)[:k]
def counts():
 with connect() as c:
  result={"documents":c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],"chunks":c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],"queries":c.execute("SELECT COUNT(*) FROM queries").fetchone()[0]}
  result.update({r["name"]:r["value"] for r in c.execute("SELECT name,value FROM counters")})
  result["errors"]={r["kind"]:r["n"] for r in c.execute("SELECT kind,COUNT(*) n FROM errors GROUP BY kind")}
  times=sorted(r[0] for r in c.execute("SELECT latency FROM queries"))
  result["median_latency_ms"]=statistics.median(times) if times else 0
  result["p95_latency_ms"]=times[max(0,math.ceil(.95*len(times))-1)] if times else 0
  return result
def log_query(q,latency,sources,error=None):
 with connect() as c: c.execute("INSERT INTO queries(question,latency,sources,error,created) VALUES(?,?,?,?,?)",(q,latency,json.dumps(sources),error,time.time()))
