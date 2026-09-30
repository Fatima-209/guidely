from fastapi import APIRouter,UploadFile,File,Form,HTTPException,Depends
from backend.auth import get_current_user,require_admin
from backend.services.store import index_document,documents,remove_document
router=APIRouter()
MAX_UPLOAD_BYTES=2*1024*1024
def safe_filename(name): return str(name or "").replace("\\","/").split("/")[-1][:255]
async def read_upload(file):
 raw=await file.read(MAX_UPLOAD_BYTES+1)
 if len(raw)>MAX_UPLOAD_BYTES:
  from backend.services.store import log_error
  log_error("upload_too_large",file.filename); raise HTTPException(413,"Maximum document size is 2 MiB")
 try: content=raw.decode("utf-8")
 except UnicodeDecodeError:
  from backend.services.store import log_error
  log_error("corrupt_file",file.filename); raise HTTPException(400,"File must be valid UTF-8 text")
 if not content.strip():
  from backend.services.store import log_error
  log_error("empty_file",file.filename); raise HTTPException(400,"Document is empty")
 return content
@router.get("/documents")
def list_documents(user=Depends(get_current_user)): return documents()
@router.post("/documents")
async def upload_document(file:UploadFile=File(...),category:str=Form("General",max_length=80),user=Depends(require_admin)):
 file.filename=safe_filename(file.filename)
 if not file.filename or not file.filename.lower().endswith((".txt",".md")):
  from backend.services.store import log_error
  log_error("unsupported_upload",file.filename); raise HTTPException(415,"Upload a .txt or .md document")
 content=await read_upload(file)
 try: return index_document(file.filename,content,category)
 except Exception as exc:
  from backend.services.store import log_error
  log_error("embedding_failure",type(exc).__name__); raise HTTPException(503,"Document indexing failed. Check the embedding service and try again.")
@router.get("/documents/{doc_id}/content")
def get_document(doc_id:str,user=Depends(get_current_user)):
 from backend.services.store import connect
 with connect() as c: row=c.execute("SELECT content FROM documents WHERE id=?",(doc_id,)).fetchone()
 if not row: raise HTTPException(404,"Document not found")
 return {"content":row["content"]}
@router.put("/documents/{doc_id}")
async def edit_document(doc_id:str,file:UploadFile=File(...),category:str=Form("General",max_length=80),user=Depends(require_admin)):
 if not any(d["id"]==doc_id for d in documents()): raise HTTPException(404,"Document not found")
 file.filename=safe_filename(file.filename)
 if not file.filename or not file.filename.lower().endswith((".txt",".md")):
  from backend.services.store import log_error
  log_error("unsupported_upload",file.filename); raise HTTPException(415,"Upload a .txt or .md document")
 content=await read_upload(file)
 try: result=index_document(file.filename,content,category)
 except Exception as exc:
  from backend.services.store import log_error
  log_error("embedding_failure",type(exc).__name__); raise HTTPException(503,"Document indexing failed. Check the embedding service and try again.")
 if result["id"]!=doc_id: remove_document(doc_id)
 return result
@router.delete("/documents/{doc_id}")
def delete_document(doc_id:str,user=Depends(require_admin)):
 if not any(d["id"]==doc_id for d in documents()): raise HTTPException(404,"Document not found")
 remove_document(doc_id); return {"deleted":True}
@router.post("/reindex")
def reindex(user=Depends(require_admin)):
 from pathlib import Path
 return {"indexed":sum(bool(index_document(p.name,p.read_text(encoding="utf-8"))) for p in Path("backend/data/sample-docs").glob("*.txt"))}
