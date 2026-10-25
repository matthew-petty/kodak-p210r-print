"""Minimal crop/compose tool for Kodak Mini prints.

Stage images in staged/, compose them in the browser, and the results go to ready/ at the exact print size
(640x1024 portrait or 1024x640 landscape). kodak_ble.py then prints them without any further cropping.

usage: uv run -q --with pillow --with pillow-heif python -I compose.py [--staged DIR] [--ready DIR] [--port N]
keys: drag = pan, wheel = zoom, R rotate, O portrait/landscape, C fill, F fit, B background, G grid,
      S/Enter save+next, arrows prev/next
"""
import argparse
import io
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

HERE = Path(__file__).resolve().parent
EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.tif', '.tiff'}
SIZES = {'p': (640, 1024), 'l': (1024, 640)}
BGS = {'#ffffff', '#000000'}
ROT = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}  # clockwise


def load(path):
    return ImageOps.exif_transpose(Image.open(path)).convert('RGB')


def render(path, p):
    im = load(path)
    if p['rot'] % 4:
        im = im.transpose(ROT[p['rot'] % 4])
    W, H = SIZES[p['orient']]
    s, x, y = float(p['s']), float(p['x']), float(p['y'])
    out = Image.new('RGB', (W, H), p['bg'])
    rw, rh = im.size
    l, t = max(0.0, -x / s), max(0.0, -y / s)
    r, b = min(rw, (W - x) / s), min(rh, (H - y) / s)
    if r > l and b > t:
        part = im.resize((max(1, round((r - l) * s)), max(1, round((b - t) * s))), Image.LANCZOS, box=(l, t, r, b))
        out.paste(part, (round(x + l * s), round(y + t * s)))
    return out


class App:
    def __init__(self, staged, ready):
        self.staged, self.ready = staged, ready
        self.db = ready / 'crops.json'
        self.crops = json.loads(self.db.read_text()) if self.db.exists() else {}
        self.previews, self.dims, self.lock = {}, {}, threading.Lock()

    def files(self):
        return {f.name: f for f in sorted(self.staged.iterdir()) if f.suffix.lower() in EXTS and not f.name.startswith('.')}

    def size(self, f):
        if f.name not in self.dims:
            with Image.open(f) as im:
                w, h = im.size
                if im.getexif().get(274, 1) in (5, 6, 7, 8):
                    w, h = h, w
            self.dims[f.name] = (w, h)
        return self.dims[f.name]

    def listing(self):
        out = []
        for name, f in self.files().items():
            w, h = self.size(f)
            done = name in self.crops and (self.ready / (f.stem + '.jpg')).exists()
            out.append({'name': name, 'w': w, 'h': h, 'done': done, 'params': self.crops.get(name)})
        return out

    def preview(self, f):
        if f.name not in self.previews:
            im = load(f)
            im.thumbnail((1600, 1600), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'JPEG', quality=85)
            self.previews[f.name] = buf.getvalue()
        return self.previews[f.name]

    def save(self, p):
        f = self.files()[p['name']]
        if p['orient'] not in SIZES or p['bg'] not in BGS or float(p['s']) <= 0:
            raise ValueError('bad params')
        p = {k: p[k] for k in ('rot', 'orient', 's', 'x', 'y', 'bg')}
        render(f, p).save(self.ready / (f.stem + '.jpg'), 'JPEG', quality=95)
        with self.lock:
            self.crops[f.name] = p
            self.db.write_text(json.dumps(self.crops, indent=1))


def handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype='application/json', code=200):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(self.path.split('?')[0])
            if path == '/':
                self.send(PAGE.encode(), 'text/html; charset=utf-8')
            elif path == '/api/list':
                self.send(json.dumps(app.listing()).encode())
            elif path.startswith('/img/') and (f := app.files().get(path[5:])):
                self.send(app.preview(f), 'image/jpeg')
            else:
                self.send(b'not found', 'text/plain', 404)

        def do_POST(self):
            if self.path != '/api/save':
                return self.send(b'not found', 'text/plain', 404)
            try:
                app.save(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send(b'{"ok":true}')
            except Exception as e:
                self.send(json.dumps({'error': str(e)}).encode(), code=400)
    return H


PAGE = r'''<!doctype html><meta charset=utf-8><title>Kodak compose</title>
<style>
html,body{margin:0;height:100%;overflow:hidden;background:#222;color:#ddd;font:13px system-ui}
#bar{height:40px;box-sizing:border-box;padding:6px 10px;background:#111;display:flex;gap:6px;align-items:center}
button{background:#333;color:#ddd;border:1px solid #555;padding:3px 8px;border-radius:3px;cursor:pointer}
#name{min-width:220px;text-align:center}#msg{margin-left:auto;color:#9c9}
canvas{display:block;cursor:grab}
</style>
<div id=bar>
<button id=prev>&#9664;</button><span id=name></span><button id=next>&#9654;</button>
<button id=rot>Rotate R</button><button id=ori>Orient O</button><button id=fill>Fill C</button>
<button id=fit>Fit F</button><button id=bg>BG B</button><button id=grid>Grid G</button><button id=save>Save S</button>
<span id=msg></span></div>
<canvas id=c></canvas>
<script>
const SIZES={p:[640,1024],l:[1024,640]}, $=id=>document.getElementById(id), cv=$('c'), ctx=cv.getContext('2d');
let items=[], i=0, img=null, st=null, fr=null, grid=true, drag=null;
const it=()=>items[i], out=()=>SIZES[st.orient];
const rdims=()=>st.rot%2?[it().h,it().w]:[it().w,it().h];
function place(cover){const [W,H]=out(),[rw,rh]=rdims(),f=cover?Math.max:Math.min;
  st.s=f(W/rw,H/rh); st.x=(W-rw*st.s)/2; st.y=(H-rh*st.s)/2; draw();}
function layout(){cv.width=innerWidth; cv.height=innerHeight-40; if(!st)return;
  const [W,H]=out(),m=40,k=Math.min((cv.width-2*m)/W,(cv.height-2*m)/H);
  fr={k,w:W*k,h:H*k,x:(cv.width-W*k)/2,y:(cv.height-H*k)/2};}
function draw(){layout(); ctx.fillStyle='#222'; ctx.fillRect(0,0,cv.width,cv.height); if(!st||!img)return;
  const [W,H]=out(),[rw,rh]=rdims(),{w,h}=it();
  ctx.save(); ctx.translate(fr.x,fr.y); ctx.scale(fr.k,fr.k);
  ctx.fillStyle=st.bg; ctx.fillRect(0,0,W,H);
  ctx.translate(st.x+rw*st.s/2,st.y+rh*st.s/2); ctx.rotate(st.rot*Math.PI/2);
  ctx.drawImage(img,-w*st.s/2,-h*st.s/2,w*st.s,h*st.s); ctx.restore();
  ctx.fillStyle='rgba(20,20,20,.75)'; ctx.beginPath(); ctx.rect(0,0,cv.width,cv.height);
  ctx.rect(fr.x,fr.y,fr.w,fr.h); ctx.fill('evenodd');
  ctx.strokeStyle='#888'; ctx.lineWidth=1; ctx.strokeRect(fr.x-.5,fr.y-.5,fr.w+1,fr.h+1);
  if(grid){ctx.strokeStyle='rgba(255,255,255,.35)'; ctx.beginPath();
    for(const t of [1/3,2/3]){ctx.moveTo(fr.x+fr.w*t,fr.y); ctx.lineTo(fr.x+fr.w*t,fr.y+fr.h);
      ctx.moveTo(fr.x,fr.y+fr.h*t); ctx.lineTo(fr.x+fr.w,fr.y+fr.h*t);} ctx.stroke();}}
function show(n){if(!items.length){$('name').textContent='no images in staged/';return;}
  i=(n+items.length)%items.length; const x=it();
  $('name').textContent=`${i+1}/${items.length} ${x.done?'✓ ':''}${x.name}`;
  st=x.params?{...x.params}:{rot:0,orient:x.w>x.h?'l':'p',bg:'#ffffff'};
  img=new Image(); img.onload=()=>{x.params?draw():place(true)}; img.src='/img/'+encodeURIComponent(x.name);}
function rotate(){const [rw,rh]=rdims(),cx=st.x+rw*st.s/2,cy=st.y+rh*st.s/2; st.rot=(st.rot+1)%4;
  const [nw,nh]=rdims(); st.x=cx-nw*st.s/2; st.y=cy-nh*st.s/2; draw();}
async function save(){const r=await fetch('/api/save',{method:'POST',body:JSON.stringify({name:it().name,...st})});
  const j=await r.json(); if(j.error){$('msg').textContent='error: '+j.error;return;}
  it().done=true; it().params={...st}; $('msg').textContent='saved '+it().name; show(i+1);}
cv.onmousedown=e=>{drag={x:e.clientX,y:e.clientY}; cv.style.cursor='grabbing'};
onmouseup=()=>{drag=null; cv.style.cursor='grab'};
onmousemove=e=>{if(!drag||!st)return; st.x+=(e.clientX-drag.x)/fr.k; st.y+=(e.clientY-drag.y)/fr.k;
  drag={x:e.clientX,y:e.clientY}; draw();};
cv.onwheel=e=>{e.preventDefault(); if(!st)return; const f=Math.exp(-e.deltaY*.0015);
  const px=(e.offsetX-fr.x)/fr.k, py=(e.offsetY-fr.y)/fr.k;
  st.x=px-(px-st.x)*f; st.y=py-(py-st.y)*f; st.s*=f; draw();};
const act={prev:()=>show(i-1),next:()=>show(i+1),rot:rotate,ori:()=>{st.orient=st.orient=='p'?'l':'p';layout();place(true)},
  fill:()=>place(true),fit:()=>place(false),bg:()=>{st.bg=st.bg=='#ffffff'?'#000000':'#ffffff';draw()},
  grid:()=>{grid=!grid;draw()},save};
for(const k in act)$(k).onclick=act[k];
const keys={ArrowLeft:'prev',ArrowRight:'next',r:'rot',o:'ori',c:'fill',f:'fit',b:'bg',g:'grid',s:'save',Enter:'save'};
onkeydown=e=>{const a=keys[e.key]; if(a&&st&&!e.metaKey&&!e.ctrlKey){e.preventDefault(); act[a]();}};
onresize=draw;
fetch('/api/list').then(r=>r.json()).then(l=>{items=l; const n=l.findIndex(x=>!x.done); show(n<0?0:n);});
</script>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--staged', type=Path, default=HERE / 'staged')
    ap.add_argument('--ready', type=Path, default=HERE / 'ready')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-open', action='store_true')
    a = ap.parse_args()
    a.staged.mkdir(exist_ok=True)
    a.ready.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), handler(App(a.staged, a.ready)))
    url = f'http://127.0.0.1:{a.port}/'
    print(f'staged: {a.staged}\nready:  {a.ready}\n{url}  (ctrl-c to quit)', flush=True)
    if not a.no_open:
        subprocess.Popen(['open', url])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
