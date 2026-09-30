import hashlib, hmac, os, re, secrets, smtplib, time
from email.message import EmailMessage
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from backend.services.store import connect, log_error
router=APIRouter(prefix="/auth",tags=["authentication"])
COOKIE="guidely_session"
SESSION_SECONDS=30*24*60*60
CODE_SECONDS=10*60

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def email_ok(value): return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value))
def code_digest(email,code):
 pepper=os.getenv("AUTH_CODE_SECRET")
 if not pepper:
  if os.getenv("GUIDELY_ENV","development").lower()=="production": raise HTTPException(503,"AUTH_CODE_SECRET must be configured")
  pepper="local-development-only-code-pepper"
 return hmac.new(pepper.encode(),f"{email}:{code}".encode(),hashlib.sha256).hexdigest()
def enforce_ip_rate(request,kind,limit,window):
 ip=request.client.host if request.client else "unknown"; now=time.time()
 with connect() as c:
  row=c.execute("SELECT window_start,count FROM auth_ip_limits WHERE ip=? AND kind=?",(ip,kind)).fetchone()
  if row and now-row["window_start"]<window:
   if row["count"]>=limit: raise HTTPException(429,"Too many attempts. Please try again later.")
   c.execute("UPDATE auth_ip_limits SET count=count+1 WHERE ip=? AND kind=?",(ip,kind))
  else: c.execute("INSERT OR REPLACE INTO auth_ip_limits VALUES(?,?,?,1)",(ip,kind,now))
def issue_email(email,code):
 host=os.getenv("SMTP_HOST")
 if not host:
  raise HTTPException(503,"Email delivery is not configured. No code was emailed. Ask the administrator to configure an email sender.")
 msg=EmailMessage(); msg["Subject"]="Your Guidely verification code"; msg["From"]=os.getenv("SMTP_FROM",os.getenv("SMTP_USERNAME","guidely@localhost")); msg["To"]=email
 msg.set_content(f"Your Guidely sign-in code is {code}. It expires in 10 minutes. If you did not request this code, you can ignore this email.")
 port=int(os.getenv("SMTP_PORT","587")); timeout=10
 try:
  if os.getenv("SMTP_SSL","0")=="1":
   with smtplib.SMTP_SSL(host,port,timeout=timeout) as server:
    if os.getenv("SMTP_USERNAME"): server.login(os.getenv("SMTP_USERNAME"),os.getenv("SMTP_PASSWORD",""))
    server.send_message(msg)
  else:
   with smtplib.SMTP(host,port,timeout=timeout) as server:
    server.ehlo()
    if os.getenv("SMTP_STARTTLS","1")=="1": server.starttls(); server.ehlo()
    if os.getenv("SMTP_USERNAME"): server.login(os.getenv("SMTP_USERNAME"),os.getenv("SMTP_PASSWORD",""))
    server.send_message(msg)
  return True
 except Exception as exc:
  log_error("email_delivery",type(exc).__name__)
  raise HTTPException(502,"We could not send the verification email. Try again shortly.")

class EmailRequest(BaseModel): email:str=Field(min_length=3,max_length=254); display_name:str=Field(default="",max_length=80)
class CodeVerify(BaseModel): email:str; code:str=Field(min_length=6,max_length=6); display_name:str=Field(default="",max_length=80)

def set_session(response,user_id):
 token=secrets.token_urlsafe(32); expires=time.time()+SESSION_SECONDS
 with connect() as c: c.execute("INSERT INTO auth_sessions(token_hash,user_id,expires) VALUES(?,?,?)",(digest(token),user_id,expires))
 response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=os.getenv("AUTH_COOKIE_SECURE","0")=="1",samesite="lax",path="/")
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
@router.post("/request-code")
def request_code(body:EmailRequest,request:Request):
 enforce_ip_rate(request,"email_codes",5,3600)
 email=body.email.strip().lower()
 if not email_ok(email): raise HTTPException(422,"Enter a valid email address")
 now=time.time()
 with connect() as c: row=c.execute("SELECT sent_at FROM login_codes WHERE email=?",(email,)).fetchone()
 if row and now-row["sent_at"]<60: raise HTTPException(429,"Please wait a minute before requesting another code")
 if not os.getenv("SMTP_HOST"):
  log_error("email_delivery","not_configured")
  raise HTTPException(503,"Email delivery is not configured. No code was emailed. Ask the administrator to configure an email sender.")
 code=f"{secrets.randbelow(1000000):06d}"
 with connect() as c: c.execute("INSERT OR REPLACE INTO login_codes(email,code_hash,expires,sent_at,attempts,display_name) VALUES(?,?,?,?,0,?)",(email,code_digest(email,code),now+CODE_SECONDS,now,body.display_name.strip()))
 try: issue_email(email,code)
 except Exception:
  with connect() as c: c.execute("DELETE FROM login_codes WHERE email=?",(email,))
  raise
 return {"message":"Verification code sent to your email"}
@router.post("/verify-code")
def verify_code(body:CodeVerify,response:Response,request:Request):
 enforce_ip_rate(request,"code_verify",30,3600)
 email=body.email.strip().lower(); now=time.time()
 with connect() as c: row=c.execute("SELECT * FROM login_codes WHERE email=?",(email,)).fetchone()
 if not row or row["expires"]<now: raise HTTPException(400,"That code expired. Request a new one.")
 if row["attempts"]>=5: raise HTTPException(429,"Too many incorrect attempts. Request a new code.")
 if not hmac.compare_digest(row["code_hash"],code_digest(email,body.code)):
  with connect() as c: c.execute("UPDATE login_codes SET attempts=attempts+1 WHERE email=?",(email,))
  raise HTTPException(400,"That verification code is incorrect")
 with connect() as c:
  user=c.execute("SELECT id,display_name FROM auth_users WHERE email=?",(email,)).fetchone()
  admins={x.strip().lower() for x in os.getenv("ADMIN_EMAILS","").split(",") if x.strip()}
  role="admin" if email in admins else "reader"
  display=body.display_name.strip() or row["display_name"] or email.split("@")[0]
  if user:
   uid=user["id"]; c.execute("UPDATE auth_users SET display_name=?,role=? WHERE id=?",(display,role,uid))
  else:
   uid=secrets.token_urlsafe(16); c.execute("INSERT INTO auth_users(id,email,display_name,role,created) VALUES(?,?,?,?,?)",(uid,email,display,role,now))
  c.execute("DELETE FROM login_codes WHERE email=?",(email,))
 set_session(response,uid); return {"id":uid,"email":email,"display_name":display,"role":role}
@router.get("/config")
def auth_config():
 default="0" if os.getenv("GUIDELY_ENV","development").lower()=="production" else "1"
 return {"guest_login":os.getenv("ALLOW_GUEST_LOGIN",default)=="1","email_delivery_configured":bool(os.getenv("SMTP_HOST"))}
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

