import os
from fastapi import FastAPI,Depends,Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.services import store
from backend.routes import documents,search
from backend.auth import router as auth_router, get_current_user
production=os.getenv("GUIDELY_ENV","development").lower()=="production"
app=FastAPI(title="Guidely Knowledge Assistant",docs_url=None if production else "/docs",redoc_url=None,openapi_url=None if production else "/openapi.json")
app.add_middleware(CORSMiddleware,allow_origins=[os.getenv("FRONTEND_ORIGIN","http://localhost:5173")],allow_methods=["*"],allow_headers=["*"] ,allow_credentials=True)
@app.middleware("http")
async def protect_api_origin(request:Request,call_next):
 origin=request.headers.get("origin")
 if request.url.path.startswith("/api/") and request.method in {"POST","PUT","PATCH","DELETE"} and origin and origin!=os.getenv("FRONTEND_ORIGIN","http://localhost:5173"):
  return JSONResponse({"detail":"Request origin is not allowed"},status_code=403)
 response=await call_next(request)
 response.headers["X-Content-Type-Options"]="nosniff"
 response.headers["X-Frame-Options"]="DENY"
 response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
 return response
@app.on_event("startup")
def startup(): store.initialize()
app.include_router(auth_router,prefix="/api")
app.include_router(documents.router,prefix="/api")
app.include_router(search.router,prefix="/api")
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/metrics")
def metrics(user=Depends(get_current_user)): return store.counts()
