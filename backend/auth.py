import hashlib, hmac, os, re, secrets, time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from backend.models.auth import AccountCredentials, LoginCredentials
from backend.services.store import connect
router=APIRouter(prefix="/auth",tags=["authentication"])
COOKIE="guidely_session"
SESSION_SECONDS=30*24*60*60
PASSWORD_ITERATIONS=310000

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def email_ok(value): return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value))
def hash_password(password,salt=None):
 salt=salt or secrets.token_bytes(16)
 result=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,PASSWORD_ITERATIONS)
 return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${result.hex()}"
def password_matches(password,stored):
 try:
  algorithm,iterations,salt,expected=stored.split("$")
  if algorithm!="pbkdf2_sha256": return False
  actual=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),int(iterations)).hex()
  return hmac.compare_digest(actual,expected)
 except (ValueError,TypeError): return False
def enforce_ip_rate(request,kind,limit,window):
 ip=request.client.host if request.client else "unknown"; now=time.time()
 with connect() as c:
  row=c.execute("SELECT window_start,count FROM auth_ip_limits WHERE ip=? AND kind=?",(ip,kind)).fetchone()
  if row and now-row["window_start"]<window:
   if row["count"]>=limit: raise HTTPException(429,"Too many attempts. Please try again later.")
   c.execute("UPDATE auth_ip_limits SET count=count+1 WHERE ip=? AND kind=?",(ip,kind))
  else: c.execute("INSERT OR REPLACE INTO auth_ip_limits VALUES(?,?,?,1)",(ip,kind,now))
def set_session(response,user_id):
 token=secrets.token_urlsafe(32); expires=time.time()+SESSION_SECONDS
 with connect() as c: c.execute("INSERT INTO auth_sessions(token_hash,user_id,expires) VALUES(?,?,?)",(digest(token),user_id,expires))
 response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=os.getenv("AUTH_COOKIE_SECURE","0")=="1",samesite=os.getenv("AUTH_COOKIE_SAMESITE","lax").lower(),path="/")
def get_current_user(request:Request):
 token=request.cookies.get(COOKIE)
 if not token: raise HTTPException(401,"Please sign in or continue as a guest")
 with connect() as c:
  row=c.execute("SELECT u.id,u.email,u.display_name,u.role FROM auth_sessions s JOIN auth_users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?",(digest(token),time.time())).fetchone()
 if not row: raise HTTPException(401,"Your session expired. Please sign in again.")
 return dict(row)
def require_admin(user=Depends(get_current_user)):
 if user["role"]!="admin": raise HTTPException(403,"Administrator access is required")
 return user

@router.get("/me")
def me(request:Request):
 try: return get_current_user(request)
 except HTTPException as e:
  if e.status_code==401: return {"user":None}
  raise
def account_role(email):
 admins={x.strip().lower() for x in os.getenv("ADMIN_EMAILS","").split(",") if x.strip()}
 return "admin" if email in admins else "reader"
@router.post("/register")
def register(body:AccountCredentials,response:Response,request:Request):
 enforce_ip_rate(request,"account_register",20,3600)
 email=body.email.strip().lower()
 if not email_ok(email): raise HTTPException(422,"Enter a valid email address")
 if account_role(email)=="admin":
  admin_key=os.getenv("ADMIN_SIGNUP_KEY","")
  if not admin_key or not hmac.compare_digest(body.admin_key,admin_key): raise HTTPException(403,"Administrator setup key is incorrect")
 now=time.time(); password_hash=hash_password(body.password); role=account_role(email)
 with connect() as c:
  user=c.execute("SELECT id,password_hash,display_name FROM auth_users WHERE email=?",(email,)).fetchone()
  if user and user["password_hash"]: raise HTTPException(409,"An account already exists. Sign in instead.")
  display=body.display_name.strip() or (user["display_name"] if user else "") or email.split("@")[0]
  if user:
   uid=user["id"]; c.execute("UPDATE auth_users SET display_name=?,role=?,password_hash=? WHERE id=?",(display,role,password_hash,uid))
  else:
   uid=secrets.token_urlsafe(16); c.execute("INSERT INTO auth_users(id,email,display_name,role,created,password_hash) VALUES(?,?,?,?,?,?)",(uid,email,display,role,now,password_hash))
 set_session(response,uid); return {"id":uid,"email":email,"display_name":display,"role":role}
@router.post("/login")
def login(body:LoginCredentials,response:Response,request:Request):
 enforce_ip_rate(request,"password_login",30,3600)
 email=body.email.strip().lower()
 if not email_ok(email): raise HTTPException(401,"Email or password is incorrect")
 with connect() as c: user=c.execute("SELECT id,email,display_name,role,password_hash FROM auth_users WHERE email=?",(email,)).fetchone()
 if not user or not user["password_hash"] or not password_matches(body.password,user["password_hash"]):
  raise HTTPException(401,"Email or password is incorrect")
 set_session(response,user["id"])
 return {"id":user["id"],"email":user["email"],"display_name":user["display_name"],"role":user["role"]}
@router.get("/config")
def auth_config():
 default="0" if os.getenv("GUIDELY_ENV","development").lower()=="production" else "1"
 return {"guest_login":os.getenv("ALLOW_GUEST_LOGIN",default)=="1"}
@router.post("/guest")
def guest(response:Response,request:Request):
 enforce_ip_rate(request,"guest_sessions",10,3600)
 if not auth_config()["guest_login"]: raise HTTPException(403,"Guest sign-in is disabled by the administrator")
 uid=secrets.token_urlsafe(16)
 with connect() as c: c.execute("INSERT INTO auth_users(id,email,display_name,role,created) VALUES(?,NULL,'Guest','guest',?)",(uid,time.time()))
 set_session(response,uid); return {"id":uid,"email":None,"display_name":"Guest","role":"guest"}
@router.post("/logout")
def logout(request:Request,response:Response):
 token=request.cookies.get(COOKIE)
 if token:
  with connect() as c: c.execute("DELETE FROM auth_sessions WHERE token_hash=?",(digest(token),))
 response.delete_cookie(COOKIE,path="/"); return {"ok":True}

