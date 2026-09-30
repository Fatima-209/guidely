import os,time,csv,io
from fastapi import APIRouter,HTTPException,Depends,Request
from backend.auth import get_current_user,require_admin,enforce_ip_rate
from fastapi.responses import StreamingResponse
from backend.models.search import Question
from backend.services import store
router=APIRouter()
def answer(q,hits,history):
 if not hits or hits[0]["score"]<=(0.18 if os.getenv("OPENAI_API_KEY") else 0.08): return "I couldn't find a relevant passage in indexed documents. Try different wording or ask an administrator to add the relevant guide."
 key=os.getenv("OPENAI_API_KEY")
 if not key and os.getenv("REQUIRE_OPENAI_API","0")=="1":
  store.log_error("missing_model_key","OPENAI_API_KEY is required")
  raise HTTPException(503,"Answer generation is unavailable because OPENAI_API_KEY is not configured")
 if key:
  try:
   from openai import OpenAI
   context="\n\n".join(f"[{i+1}] {h['document']} / {h['section']}: {h['snippet']}" for i,h in enumerate(hits[:4]))
   msgs=[{"role":"system","content":"Use only supplied excerpts, answer concisely, cite sources as [1]. If excerpts don't answer, say so."}]
   msgs += [{"role":m.role,"content":m.content} for m in history[-6:]]
   msgs.append({"role":"user","content":f"{q}\n\n{context}"})
   return OpenAI(api_key=key,timeout=12).chat.completions.create(model=os.getenv("OPENAI_CHAT_MODEL","gpt-4o-mini"),messages=msgs).choices[0].message.content
  except Exception as e:
   store.log_error("llm_failure",type(e).__name__)
   if os.getenv("REQUIRE_OPENAI_API","0")=="1": raise HTTPException(503,"The answer model is unavailable. Please try again later.")
 return hits[0]["snippet"][:650]+"\n\nBased on the most relevant indexed passage."
@router.post("/ask")
def ask(body:Question,request:Request,user=Depends(get_current_user)):
 enforce_ip_rate(request,"search",60,60)
 q=body.question.strip(); start=time.perf_counter()
 if not q:
  store.log_error("empty_query","Enter a question to search the knowledge base"); raise HTTPException(400,"Enter a question to search the knowledge base")
 try:
  hits=store.retrieve(q,body.top_k,body.category)
  if not hits or hits[0]["score"]<=(0.18 if os.getenv("OPENAI_API_KEY") else 0.08):
   ms=round((time.perf_counter()-start)*1000,2); store.log_query(q,ms,[],"no_results"); store.log_error("no_results","No relevant passages found"); raise HTTPException(404,"No relevant passages found. Try different wording or ask an administrator to add the relevant guide.")
  text=answer(q,hits,body.history); ms=round((time.perf_counter()-start)*1000,2)
  sources=[{k:h[k] for k in ("id","document","category","section","snippet","score")} for h in hits if h["score"]>0][:5]
  store.log_query(q,ms,[s["id"] for s in sources]); return {"answer":text,"sources":sources,"latency_ms":ms}
 except HTTPException: raise
 except store.EmbeddingProviderError as e:
  ms=round((time.perf_counter()-start)*1000,2); store.log_query(q,ms,[],"embedding_provider"); store.log_error("embedding_failure",str(e))
  raise HTTPException(503,"Embedding service unavailable. Check OPENAI_API_KEY and try again.")
 except Exception as e:
  ms=round((time.perf_counter()-start)*1000,2); store.log_query(q,ms,[],type(e).__name__); store.log_error("search_failure",type(e).__name__); raise HTTPException(500,"Search failed. Please try again.")
@router.get("/export-queries")
def export_queries(user=Depends(require_admin)):
 with store.connect() as c: rows=c.execute("SELECT created,question,latency,sources,error FROM queries ORDER BY created DESC").fetchall()
 s=io.StringIO(); w=csv.writer(s); w.writerow(["timestamp","question","latency_ms","sources","error"]); [w.writerow(list(r)) for r in rows]; s.seek(0)
 return StreamingResponse(iter([s.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=query-logs.csv"})
