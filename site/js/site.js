/* Vault OS site. One ES module, no dependencies.
   Motion: IntersectionObserver + a small rAF spring + WAAPI (SITE-SPEC.md section 11). Only transform, opacity and clip-path animate. */
const root=document.documentElement;
const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
const mq=q=>matchMedia(q);
const RM=mq('(prefers-reduced-motion: reduce)');
const reduce=()=>RM.matches;
const E_OUT='cubic-bezier(.23,1,.32,1)',E_IO='cubic-bezier(.77,0,.175,1)';
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const vis=el=>!!(el&&(el.offsetWidth||el.offsetHeight||el.getClientRects().length));
const pinMq=mq('(min-height:640px) and (min-width:360px)');

/* ---------- spring (SITE-SPEC.md 11): critically damped by default, keeps the presentation value, so it is interruptible ---------- */
function spring({ratio=1,response=.35}={}){
  const k=(2*Math.PI/response)**2,c=4*Math.PI*ratio/response;
  let x=0,v=0,target=0,raf=0,onFrame=()=>{},last=0;
  const step=t=>{let dt=Math.min(.064,(t-last)/1000);last=t;
    while(dt>0){const h=Math.min(dt,1/240);v+=(-k*(x-target)-c*v)*h;x+=v*h;dt-=h}
    onFrame(x);
    if(Math.abs(v)<.01&&Math.abs(x-target)<.1){x=target;v=0;onFrame(x);raf=0;return}
    raf=requestAnimationFrame(step)};
  return{set(to,{velocity}={}){target=to;if(velocity!==undefined)v=velocity;
      if(!raf){last=performance.now();raf=requestAnimationFrame(step)}},
    jump(to){x=target=to;v=0;onFrame(x)},frame(fn){onFrame=fn},get value(){return x}};
}
/* spring sampled to WAAPI keyframes (pop .72/.30 for tags and arrivals) */
function springKF(ratio,response,from,to){
  const k=(2*Math.PI/response)**2,c=4*Math.PI*ratio/response,h=1/240;let x=from,v=0,t=0;const pts=[[0,from]];
  for(let i=1;i<240*1.2;i++){v+=(-k*(x-to)-c*v)*h;x+=v*h;t+=h;if(i%6===0)pts.push([t,x]);if(i>30&&Math.abs(v)<.02&&Math.abs(x-to)<.002){break}}
  pts.push([t,to]);return{dur:t*1000,pts};
}
const project=(v,d=.998)=>(v/1000)*d/(1-d);
const rubber=(o,dim,c=.55)=>(o*dim*c)/(dim+c*Math.abs(o));

/* ---------- WAAPI helpers (fill backwards: the end state is the stylesheet's own) ---------- */
const fx=(el,kf,o={})=>el.animate(kf,{duration:240,easing:E_OUT,fill:'backwards',...o});
const rise=(el,{y=6,dur=240,delay=0,fill}={})=>fx(el,[{opacity:0,transform:`translateY(${y}px)`},{opacity:1,transform:'translateY(0)'}],{duration:dur,delay,...(fill?{fill}:{})});
const popin=(el,{from=.96,dur=240,delay=0,fill}={})=>fx(el,[{opacity:0,transform:`scale(${from})`},{opacity:1,transform:'scale(1)'}],{duration:dur,delay,...(fill?{fill}:{})});
const fadein=(el,{dur=150,delay=0,fill}={})=>fx(el,[{opacity:0},{opacity:1}],{duration:dur,delay,easing:'linear',...(fill?{fill}:{})});
/* spring pop: scale from..1 with the pop spring, opacity in over the first 60 ms */
function springPop(el,{from=.92,delay=0,ratio=.72,response=.30,fill='backwards'}={}){
  const s=springKF(ratio,response,from,1);
  const T=s.dur/1000,kf=s.pts.map(([t,x],i,arr)=>({offset:i===arr.length-1?1:Math.min(.999,t/T),opacity:Math.min(1,t/.06),transform:`scale(${x.toFixed(4)})`}));
  return el.animate(kf,{duration:s.dur,delay,easing:'linear',fill});
}
const stag=(els,fn,gap,start=0)=>els.map((el,i)=>fn(el,start+i*gap));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

/* ---------- dot-matrix numerals (port of DM and dm() from the app's core.js) ---------- */
const DM={'0':['.###.','#...#','#...#','#...#','#...#','#...#','.###.'],'1':['..#..','.##..','..#..','..#..','..#..','..#..','.###.'],
'2':['.###.','#...#','....#','...#.','..#..','.#...','#####'],'3':['.###.','#...#','....#','..##.','....#','#...#','.###.'],
'4':['...#.','..##.','.#.#.','#..#.','#####','...#.','...#.'],'5':['#####','#....','####.','....#','....#','#...#','.###.'],
'6':['..##.','.#...','#....','####.','#...#','#...#','.###.'],'7':['#####','....#','...#.','..#..','.#...','.#...','.#...'],
'8':['.###.','#...#','#...#','.###.','#...#','#...#','.###.'],'9':['.###.','#...#','#...#','.####','....#','...#.','.##..'],
'%':['##...','##..#','...#.','..#..','.#...','#..##','...##'],'.':['.','.','.','.','.','.','#'],',':['..','..','..','..','..','.#','#.'],
'/':['....#','...#.','...#.','..#..','.#...','.#...','#....'],':':['.','.','#','.','#','.','.'],' ':['..','..','..','..','..','..','..']};
function dm(str,h,cls=''){str=String(str);const p=h/7,r=p*.4;let x=0,c='';
  for(const ch of str){const g=DM[ch];if(!g){x+=2*p;continue}
    g.forEach((row,ri)=>[...row].forEach((v,ci)=>{if(v==='#')c+=`<circle cx="${(x+ci*p+p/2).toFixed(2)}" cy="${(ri*p+p/2).toFixed(2)}" r="${r.toFixed(2)}"/>`}));
    x+=(g[0].length+1)*p}
  x-=p;const t=document.createElement('template');
  t.innerHTML=`<svg class="dm ${cls}" viewBox="0 0 ${x.toFixed(1)} ${h}" width="${x.toFixed(1)}" height="${h}" role="img" aria-label="${str}"><g fill="currentColor">${c}</g></svg>`;
  return t.content.firstChild;}
/* numeral roll (M15): old out translateY 0 to -.62em with a fade, new in from +.62em */
function roll(host,str,h){
  const old=host.firstElementChild,nu=dm(str,h,old?old.getAttribute('class').replace(/^dm\s*/,''):'');
  if(old&&old.getAttribute('aria-label')===String(str))return;
  const y=h*.62;
  if(!old||reduce()){host.replaceChildren(nu);return}
  host.style.position='relative';host.style.display='inline-block';
  old.style.cssText+='position:absolute;left:0;top:0';host.appendChild(nu);
  const a=old.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:`translateY(${-y}px)`}],{duration:220,easing:E_OUT,fill:'forwards'});
  nu.animate([{opacity:0,transform:`translateY(${y}px)`},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:E_OUT,fill:'backwards'});
  a.onfinish=()=>old.remove();
}

/* ---------- sequences: a group of WAAPI animations plus a clock for discrete events; pause on leave, hidden tab, user pause ---------- */
class Seq{
  constructor(o){
    Object.assign(this,{noun:'sequence',thresh:.5,auto:false,state:'idle',anims:[],events:[],userPause:false,viewPause:false,clock:null,raf:0,btn:null,target:null},o);
    if(this.btn)this.btn.addEventListener('click',()=>this.press());
    if(this.target)new IntersectionObserver(es=>{const e=es[es.length-1];this.viewPause=!e.isIntersecting;
      if(this.auto&&this.state==='idle'&&!this.started&&e.intersectionRatio>=this.thresh&&!reduce())this.start();this.sync()},{threshold:[0,this.thresh]}).observe(this.target);
    document.addEventListener('visibilitychange',()=>this.sync());
    this.setBtn('replay');
  }
  setBtn(kind){const b=this.btn;if(!b)return;
    const m={pause:['i-pause','Pause the '],resume:['i-play','Resume the '],replay:['i-rotate','Replay the ']}[kind];
    const u=$('use',b);if(u)u.setAttribute('href','#'+m[0]);b.setAttribute('aria-label',m[1]+this.noun);}
  start(opts={}){
    if(reduce())return;this.stop();this.reset&&this.reset(opts);
    const b=this.build(opts);this.anims=b.anims;this.events=b.events.slice().sort((x,y)=>x[0]-y[0]);
    const clock=new Animation(new KeyframeEffect(null,null,{duration:Math.max(1,b.total)}),document.timeline);
    this.clock=clock;this.state='playing';this.started=true;this.userPause=false;clock.play();this.setBtn('pause');
    clock.finished.then(()=>{if(this.clock===clock)this.finish()}).catch(()=>{});
    this.sync();
  }
  poll(){cancelAnimationFrame(this.raf);const tick=()=>{if(!this.clock)return;const t=this.clock.currentTime||0;
      while(this.events.length&&this.events[0][0]<=t)this.events.shift()[1]();this.raf=requestAnimationFrame(tick)};this.raf=requestAnimationFrame(tick)}
  finish(){while(this.events.length)this.events.shift()[1]();cancelAnimationFrame(this.raf);this.state='done';this.setBtn('replay');this.onDone&&this.onDone()}
  stop(){this.anims.forEach(a=>a.cancel());this.anims=[];if(this.clock)this.clock.cancel();this.clock=null;cancelAnimationFrame(this.raf);this.events=[];this.state='idle'}
  sync(){
    if(this.state!=='playing')return;
    const run=!this.userPause&&!this.viewPause&&!document.hidden;
    for(const a of [...this.anims,this.clock]){if(run){if(a.playState==='paused')a.play()}else if(a.playState==='running')a.pause()}
    if(run)this.poll();else cancelAnimationFrame(this.raf);
  }
  press(){
    if(this.state==='playing'){this.userPause=!this.userPause;this.setBtn(this.userPause?'resume':'pause');this.sync()}
    else this.start({replay:true});
  }
}

/* ---------- --k for every .fit (SITE-SPEC.md 5.1): min(1, boxWidth/naturalWidth, boxHeight/naturalHeight) ---------- */
function initFit(){
  const fits=$$('.fit');
  const calc=f=>{
    const inn=f.firstElementChild;if(!inn||!inn.classList.contains('fit-in'))return;
    if(getComputedStyle(inn).position!=='absolute'){f.style.removeProperty('--k');f.style.removeProperty('height');return}
    const cs=getComputedStyle(f),nw=parseFloat(cs.getPropertyValue('--w')),nh=parseFloat(cs.getPropertyValue('--h'));
    if(!nw||!nh)return;
    let k=Math.min(1,f.clientWidth/nw);
    const pinned=root.classList.contains('js')&&pinMq.matches;
    let avail=Infinity;
    const card=f.closest('.card');
    if(card&&pinned){
      const ccs=getComputedStyle(card),vis_=f.closest('.vis');
      let used=0;for(const ch of card.children){if(ch===vis_)continue;const s=getComputedStyle(ch);used+=ch.offsetHeight+parseFloat(s.marginTop)+parseFloat(s.marginBottom)}
      avail=card.clientHeight-parseFloat(ccs.paddingTop)-parseFloat(ccs.paddingBottom)-used;
    }else if(f.classList.contains('sv')&&pinned){
      const st=f.closest('.step-sticky');if(st)avail=st.clientHeight-(innerWidth>=1024?96:200);
    }
    if(avail<Infinity&&avail>0)k=Math.min(k,avail/nh);
    k=Math.max(.2,+k.toFixed(4));
    if(f.__k!==k){f.__k=k;f.style.setProperty('--k',k)}
    f.style.height=(nh*k)+'px';
  };
  const ro=new ResizeObserver(es=>{for(const e of es)e.target.classList.contains('fit')?calc(e.target):$$('.fit',e.target).forEach(calc)});
  fits.forEach(f=>{ro.observe(f);const c=f.closest('.card');if(c)ro.observe(c);const s=f.closest('.step-sticky');if(s)ro.observe(s)});
  fits.forEach(calc);
  addEventListener('resize',()=>fits.forEach(calc));
  if(document.fonts)document.fonts.ready.then(()=>fits.forEach(calc));
  window.__fitCalc=()=>fits.forEach(calc);
}

/* ---------- reveals: hero intro (M1) and chapter headers (M5) ---------- */
function initReveals(){
  const hero=$$('#top [data-reveal]');
  if(!reduce())hero.forEach((el,i)=>fx(el,[{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:280,delay:i*50}));
  root.classList.add('hero-in');
  const heads=$$('.ch-hd[data-reveal]');
  const io=new IntersectionObserver(es=>{for(const e of es){if(!e.isIntersecting)continue;io.unobserve(e.target);
      const kids=Array.from(e.target.children);e.target.classList.add('in');
      if(reduce())kids.forEach(k=>fadein(k));else kids.forEach((k,i)=>fx(k,[{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:280,delay:i*60}))}},{threshold:.25});
  heads.forEach(h=>io.observe(h));
}

/* ---------- path-following token ---------- */
const bez=(a,b,c,d)=>{const f=(t,p,q)=>3*(1-t)*(1-t)*t*p+3*(1-t)*t*t*q+t*t*t;
  return x=>{if(x<=0)return 0;if(x>=1)return 1;let lo=0,hi=1,t=x;for(let i=0;i<28;i++){const v=f(t,a,c);if(Math.abs(v-x)<1e-5)break;v<x?lo=t:hi=t;t=(lo+hi)/2}return f(t,b,d)}};
const eIO=bez(.77,0,.175,1);
/* 24 points along a path, spaced by the eased arc length so linear keyframes give an eased move */
function pathPts(path,map,{rev=false,n=24,ease=eIO}={}){
  const len=path.getTotalLength(),pts=[];
  for(let i=0;i<=n;i++){let l=len*ease(i/n);if(rev)l=len-l;const p=path.getPointAtLength(l);pts.push(map(p.x,p.y))}
  return pts;
}
/* one token animation over a list of timed point groups: [[t0, pts, travelMs], ...] */
function tokenAnim(token,groups,total,{fadeIn=90,fadeOut=160}={}){
  const kf=[];const o=t=>Math.min(1,Math.max(0,t/total));
  groups.forEach(([t0,pts,tr],gi)=>{pts.forEach(([x,y],i)=>{
    const t=t0+tr*i/(pts.length-1);
    kf.push({offset:o(t),opacity:(gi===0&&i===0)?0:1,transform:`translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`})})});
  const last=kf[kf.length-1];
  if(kf[0]&&kf[1])kf[1]={...kf[1],offset:Math.max(kf[1].offset,o(fadeIn))};
  kf.push({offset:o(Math.min(total,groups[groups.length-1][0]+groups[groups.length-1][2]+fadeOut)),opacity:0,transform:last.transform});
  /* make offsets monotonic and the last one 1 */
  for(let i=1;i<kf.length;i++)if(kf[i].offset<kf[i-1].offset)kf[i].offset=kf[i-1].offset;
  kf[kf.length-1].offset=1;
  return token.animate(kf,{duration:total,easing:'linear',fill:'none'});
}
/* arrival pop on a card: halo (::after) 0 to 1 to .35, scale 1.03 to 1 with the pop spring */
function arrive(card,{halo=true,rest=.35}={}){
  const out=[];
  if(halo)out.push(card.animate([{opacity:0},{opacity:1,offset:.3},{opacity:rest}],{duration:380,easing:E_OUT,pseudoElement:'::after',fill:'forwards'}));
  const s=springKF(.72,.30,1.03,1);
  out.push(card.animate(s.pts.map(([t,x])=>({offset:Math.min(1,t/(s.dur/1000)),transform:`scale(${x})`})),{duration:s.dur,easing:'linear'}));
  return out;
}
const bindMap=(svg,box)=>{const m=svg.getScreenCTM(),r=box.getBoundingClientRect();return(x,y)=>{const p=new DOMPoint(x,y).matrixTransform(m);return[p.x-r.left,p.y-r.top]}};
const inView=(el,thresh)=>new Promise(res=>{const io=new IntersectionObserver(es=>{if(es.some(e=>e.isIntersecting)){io.disconnect();res()}},{threshold:thresh});io.observe(el)});
const fontsReady=()=>Promise.race([document.fonts?document.fonts.ready:Promise.resolve(),sleep(600)]);
const rmFade=(els,dur=150)=>els.forEach(el=>el.animate([{opacity:0},{opacity:1}],{duration:dur,easing:'linear'}));

/* ---------- hero router (M2, M3, M3a, M3b, M4) ---------- */
function initRouter(){
  const R=$('[data-router]');if(!R)return;
  const box=$('.router-box',R),token=$('[data-token]',R),cap=$('[data-router-caption]',R),caps=R.dataset.captions.split('|');
  const full=cap.textContent,tier=n=>$(`[data-tier="${n}"]`,R),task=$('[data-node="task"]',R),you=$('[data-node="you"]',R);
  const cards=[task,tier('haiku'),tier('sonnet'),tier('opus'),you];
  const hops=[['task-opus',0,'opus'],['sonnet-opus',1,'opus','sonnet'],['haiku-sonnet',1,'sonnet','haiku'],['haiku-sonnet',0,'haiku','sonnet'],['sonnet-opus',0,'sonnet','opus'],['opus-you',0,'opus','you']];
  /* hop = [path name, reversed, from, to] */
  const H=[['task-opus',0,'task','opus'],['sonnet-opus',1,'opus','sonnet'],['haiku-sonnet',1,'sonnet','haiku'],['haiku-sonnet',0,'haiku','sonnet'],['sonnet-opus',0,'sonnet','opus'],['opus-you',0,'opus','you']];
  const cardOf=k=>k==='task'?task:k==='you'?you:tier(k);
  const swapCap=(text,instant)=>{
    if(instant||reduce()){cap.textContent=text;return}
    cap.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(-4px)'}],{duration:100,easing:E_OUT,fill:'forwards'}).onfinish=function(){
      cap.textContent=text;this.cancel();cap.animate([{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:E_OUT})}};
  const visWires=()=>$$('.wires',R).find(vis);
  let breath=null;
  const seq=new Seq({noun:'route',target:R,thresh:.3,btn:$('[data-replay]',R),
    reset(){cap.getAnimations().forEach(a=>a.cancel());cap.textContent=reduce()?full:'';breath&&breath.cancel();
      cards.forEach(c=>c.getAnimations({subtree:false}).forEach(a=>a.cancel()))},
    build(){
      const svg=visWires(),map=bindMap(svg,box),paths=Object.fromEntries($$('path[data-hop]',svg).map(p=>[p.dataset.hop,p]));
      const groups=H.map(([name,rev],i)=>[i*900,pathPts(paths[name],map,{rev}),520]);
      const total=6*900,anims=[tokenAnim(token,groups,total)],events=[];
      H.forEach(([,,from,to],i)=>{
        events.push([i*900,()=>swapCap(caps[i])]);
        events.push([i*900+520,()=>{seq.anims.push(...arrive(cardOf(to),{rest:to==='you'?.5:.35}))}])});
      return{anims,events,total};
    },
    onDone(){if(!reduce()){const h=you.animate([{opacity:.35},{opacity:.8,offset:.5},{opacity:.35}],{duration:2400,iterations:2,easing:E_IO,pseudoElement:'::after'});breath=h}}
  });
  /* the arrival animations are created while playing, so add them to the group when they start */
  const origSync=seq.sync.bind(seq);
  const showAll=()=>{R.classList.add('built');if(reduce()){rmFade([...cards,...$$('.wires',R).filter(vis)])}};
  /* the route starts only when the owner card is fully visible and clear of the dock (A6) */
  const clear=()=>{const r=you.getBoundingClientRect();
    if(!r.width||r.top<0||r.left<0||r.bottom>innerHeight||r.right>innerWidth)return false;
    const d=$('[data-dock]');if(!d||d.classList.contains('is-away'))return true;
    const k=d.getBoundingClientRect();
    return !(k.width&&r.left<k.right&&r.right>k.left&&r.top<k.bottom&&r.bottom>k.top)};
  const whenClear=()=>new Promise(res=>{let raf=0;
    const io=new IntersectionObserver(()=>sched(),{threshold:[0,1]});
    const done=()=>{io.disconnect();removeEventListener('scroll',sched);removeEventListener('resize',sched)};
    const chk=()=>{raf=0;if(clear()){done();res()}};
    const sched=()=>{raf||(raf=requestAnimationFrame(chk))};
    io.observe(you);addEventListener('scroll',sched,{passive:true});addEventListener('resize',sched);sched()});
  if(!reduce()){R.classList.add('route-wait');cap.textContent=''}
  (async()=>{
    await Promise.all([fontsReady(),inView(R,.3)]);
    if(reduce()){showAll();return}
    R.classList.add('built');
    cards.forEach((c,i)=>fx(c,[{opacity:0,transform:'scale(.96)'},{opacity:1,transform:'scale(1)'}],{duration:280,delay:i*60}));
    const w=visWires();if(w)w.animate([{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0 0 0)'}],{duration:400,delay:4*60+280,easing:E_OUT,fill:'backwards'});
    await sleep(4*60+280+400+200);
    await whenClear();
    await sleep(200);
    R.classList.remove('route-wait');
    seq.start();
  })();
  R.__seq=seq;
}

/* ---------- review loop (M16) ---------- */
function initLoop(){
  const L=$('[data-loop]');if(!L)return;
  const lv=()=>$$('.loopv',L).find(vis);
  const pipsOf=v=>$$('.pp',v),ghost=v=>$('.ln.ghost',v),you=v=>$('[data-node="you"]',v);
  const all=sel=>$$(sel,L);
  const idle=()=>{L.dataset.state='idle';all('.pp').forEach(p=>p.classList.remove('on'));all('.ln.ghost').forEach(g=>g.classList.remove('on'));all('[data-node="you"]').forEach(y=>y.classList.remove('lit'))};
  const finish=()=>{L.dataset.state='finished';all('.pp').forEach(p=>p.classList.add('on'));all('.ln.ghost').forEach(g=>g.classList.add('on'));all('[data-node="you"]').forEach(y=>y.classList.add('lit'))};
  /* segments of 13: [name, reversed] */
  const ROUTE=[['n0'],['n1'],['back'],['n0'],['n1'],['back'],['branch2',1],['branch2'],['n0'],['n1'],['n2'],['n3'],['n4']];
  let token=null;
  const seq=new Seq({noun:'loop',target:L,thresh:.5,auto:true,btn:$('[data-replay]',L),
    reset(){idle();if(token){token.remove();token=null}},
    build(){
      const v=lv(),inn=$('.fit-in',v),svg=$('.lsvg',v);
      token=document.createElement('i');token.className='token';token.setAttribute('aria-hidden','true');inn.appendChild(token);
      const paths=Object.fromEntries($$('path[data-seg]',svg).map(p=>[p.dataset.seg,p]));
      const map=(x,y)=>[x,y],T=580;
      const groups=ROUTE.map(([n,rev],i)=>[i*T,pathPts(paths[n],map,{rev}),420]);
      const total=ROUTE.length*T,events=[],anims=[tokenAnim(token,groups,total)];
      const pips=pipsOf(v),gh=ghost(v),yo=you(v);
      events.push([2*T+420,()=>pips[0]&&pips[0].classList.add('on')]);
      events.push([5*T+420,()=>pips[1]&&pips[1].classList.add('on')]);
      events.push([6*T+420,()=>gh.classList.add('on')]);
      events.push([12*T+420,()=>{yo.classList.add('lit');seq.anims.push(...arrive(yo,{rest:1}))}]);
      return{anims,events,total};
    },
    onDone(){L.dataset.state='finished'}
  });
  if(reduce())finish();else idle();
  L.__seq=seq;
  /* a resize swaps the visible variant: stop and show the finished state */
  matchMedia('(min-width:768px)').addEventListener('change',()=>{seq.stop();finish()});
  matchMedia('(min-width:1280px)').addEventListener('change',()=>{seq.stop();finish()});
}

/* ---------- memory ladder (M18) ---------- */
function initLadder(){
  const W=$('[data-ladder]');if(!W)return;
  const rows=$$('.lrow',W),byLv=l=>rows.find(r=>r.dataset.level==String(l));
  const chip=$('[data-fact]',W),dot=$('[data-fact-dot]',W),gates=$$('.gate',W),seal=$('.seal',W);
  const mobile=()=>!vis(chip),mover=()=>mobile()?dot:chip;
  const cy=el=>{const r=el.getBoundingClientRect();return r.top+r.height/2};
  const natural=m=>{const t=m.style.transform;m.style.transform='';const c=cy(m);m.style.transform=t;return c};
  const yOf=(m,l)=>cy(byLv(l))-natural(m);
  const nx=document.createElement('span');nx.className='t-cap ink-2 nx';nx.textContent='next: your approval';nx.hidden=true;byLv(2).querySelector('.lt').appendChild(nx);
  const ftag=$('.fact-tag',W),placeTag=lv=>{const h=ftag&&byLv(lv).querySelector('.lt');if(h&&ftag.parentNode!==h)h.appendChild(ftag)};
  const setReached=(lv,on)=>{rows.forEach(r=>{if(+r.dataset.level<=lv)r.classList.toggle('reached',on)});if(on)placeTag(lv)};
  const clearMovers=()=>[chip,dot].forEach(m=>{m.getAnimations().forEach(a=>a.cancel());m.style.transform=''});
  const state=s=>{W.dataset.state=s;W.classList.toggle('at3',s==='finished')};
  const idle=()=>{state('idle');clearMovers();rows.forEach(r=>r.classList.remove('reached'));byLv(0).classList.add('reached');placeTag(0);gates.forEach(g=>g.classList.remove('on'));seal.classList.remove('on');nx.hidden=true;
    const m=mover();m.style.transform=`translateY(${yOf(m,0)}px)`};
  const finish=()=>{state('finished');clearMovers();placeTag(3);rows.forEach(r=>r.classList.add('reached'));gates.forEach(g=>g.classList.add('on'));seal.classList.add('on');nx.hidden=true};
  const rm=()=>{state('rm');clearMovers();placeTag(2);rows.forEach(r=>r.classList.toggle('reached',+r.dataset.level<=2));gates.forEach((g,i)=>g.classList.toggle('on',i>=1));seal.classList.remove('on');nx.hidden=false;
    const m=mover();m.style.transform=`translateY(${yOf(m,2)}px)`};
  const rest=()=>reduce()?rm():finish();
  const seq=new Seq({noun:'promotion',target:W,thresh:.5,auto:true,btn:$('[data-replay]',W),
    reset(){idle();state('playing')},
    build(){
      const m=mover();m.style.transform='';
      const y=l=>yOf(m,l),yg=cy(gates[0])-natural(m);
      const T=[[0,y(0)],[420,y(1)],[720,y(1)],[1140,y(2)],[1440,y(2)],[1650,yg],[2250,yg],[2460,y(3)],[2760,y(3)]];
      const kf=T.map(([t,yy],i)=>({offset:t/2760,transform:`translateY(${yy.toFixed(1)}px)`,easing:[0,2,4,6].includes(i)?E_IO:'linear'}));
      kf[0].easing=E_IO;kf[2].easing=E_IO;kf[4].easing=E_IO;kf[6].easing=E_IO;
      kf[1].easing='linear';kf[3].easing='linear';kf[5].easing='linear';kf[7].easing='linear';
      const anim=m.animate(kf,{duration:2760,fill:'both'});
      const gl=[gates[2],gates[1],gates[0]],ev=[];
      ev.push([210,()=>gl[0].classList.add('on')]);
      ev.push([420,()=>setReached(1,true)]);
      ev.push([930,()=>gl[1].classList.add('on')]);
      ev.push([1140,()=>setReached(2,true)]);
      ev.push([1650,()=>gl[2].classList.add('on')]);
      ev.push([2250,()=>{seal.classList.add('on');seq.anims.push(springPop(seal,{from:.92}))}]);
      ev.push([2460,()=>{setReached(3,true);W.classList.add('at3')}]);
      return{anims:[anim],events:ev,total:2760};
    },
    onDone(){finish()}
  });
  reduce()?rm():idle();
  W.__seq=seq;
  let w=innerWidth;addEventListener('resize',()=>{if(innerWidth!==w&&seq.state!=='playing'){w=innerWidth;seq.state==='done'||!seq.started?(reduce()?rm():(seq.started?finish():idle())):0}});
}

/* ---------- evolution dial (M19, M20): idle until "Run evolution" ---------- */
function initDial(){
  const E=$('[data-evo]');if(!E)return;
  const ticks=$$('.dsvg .tk',E),sweep=$('[data-sweep]',E),numHost=$('[data-evo-num]',E),txt=$('[data-evo-text]',E),phases=$$('[data-phase]',E),run=$('[data-run]'),res=$('[data-evo-result]');
  const runL=$('span',run),NAMES=['Curator','Hygiene','Retrieval refresh','Verifier'],IDLE='Idle · runs only when you ask';
  const dmH=48,dmCls=()=>{const s=numHost.firstElementChild;return s?s.getAttribute('class').replace(/^dm\s*/,''):''};
  const setNum=(str,instant)=>{const first=numHost.firstElementChild;if(!first){numHost.appendChild(dm(str,dmH));return}
    if(instant||reduce()){numHost.replaceChildren(dm(str,dmH,dmCls()));return}roll(numHost,str,dmH)};
  const q=n=>ticks.filter(t=>t.dataset.q==String(n));
  const setPhase=n=>phases.forEach((p,i)=>p.classList.toggle('on',i===n));
  const idle=()=>{E.dataset.state='idle';ticks.forEach(t=>{t.getAnimations().forEach(a=>a.cancel());t.classList.remove('on')});res.style.visibility='hidden';setPhase(-1);numHost.style.cssText='';numHost.replaceChildren(dm('0/4',dmH,dmCls()));txt.textContent=IDLE;sweep.classList.remove('show');runL.textContent='Run evolution';run.disabled=false};
  const done=()=>{E.dataset.state='done';ticks.forEach(t=>{t.classList.add('on');t.getAnimations().forEach(a=>a.cancel())});setPhase(3);numHost.replaceChildren(dm('4/4',dmH,dmCls()));txt.textContent='Done';res.style.visibility='';sweep.classList.remove('show');runL.textContent='Run again';run.disabled=false;
    if(!reduce())$$('p',res).forEach((p,i)=>rise(p,{delay:i*80}));else rmFade($$('p',res))};
  const seq=new Seq({noun:'evolution',target:E,thresh:.2,
    reset(){idle();E.dataset.state='running';run.disabled=true;runL.textContent='Running'},
    build(){
      const anims=[],ev=[],Q=700;
      for(let n=0;n<4;n++){
        q(n+1).forEach((t,i)=>anims.push(t.animate([{opacity:.18},{opacity:1}],{delay:n*Q+i*14,duration:160,easing:E_OUT,fill:'both'})));
        ev.push([n*Q,()=>{q(n+1).forEach(t=>t.classList.add('on'));setPhase(n);txt.textContent=NAMES[n]}]);
        ev.push([(n+1)*Q-40,()=>setNum((n+1)+'/4')])}
      sweep.classList.add('show');
      anims.push(sweep.animate([{transform:'rotate(0deg)',opacity:1},{transform:'rotate(360deg)',opacity:1}],{duration:4*Q,easing:'linear',fill:'both'}));
      return{anims,events:ev,total:4*Q};
    },
    onDone(){done()}
  });
  run.addEventListener('click',()=>{
    if(run.disabled)return;
    if(reduce()){ /* quadrants light whole, 300 ms apart, 150 ms fade; no sweep */
      idle();E.dataset.state='running';run.disabled=true;runL.textContent='Running';
      [0,1,2,3].forEach(n=>setTimeout(()=>{q(n+1).forEach(t=>{t.classList.add('on');t.animate([{opacity:.18},{opacity:1}],{duration:150,easing:'linear'})});setPhase(n);txt.textContent=NAMES[n];setNum((n+1)+'/4',true);
        if(n===3)setTimeout(done,300)},n*300));return}
    seq.start();
  });
  idle();
  E.__seq=seq;
}

/* ---------- guard rows (M17) ---------- */
function initGuards(){
  const rows=$$('[data-guard]');if(!rows.length)return;
  const host=rows[0].parentElement;
  new IntersectionObserver((es,io)=>{if(!es.some(e=>e.isIntersecting))return;io.disconnect();
    host.classList.add('in');
    if(reduce()){rmFade(rows);return}
    rows.forEach((r,i)=>rise(r,{delay:i*80}));
    rows.forEach((r,i)=>springPop($('.gt',r),{from:.92,delay:rows.length*80+240+i*160}))},{threshold:.3}).observe(host);
}

/* ---------- pinned decks (Ch1, Ch6) and the context stepper (Ch3): SITE-SPEC.md 8.2, 8.3 ---------- */
/* M8: the visual of a card builds when it becomes active. Groups run one after another, rows stagger inside a group. */
const CARD_SEQ=[
  ['.brief .br','row',40],['.v2 .rul','row',40],['.v2 .dev','pop',60],['.skl>*','pop',50],
  ['.v-sample:not(.v4) .sample .pl','row',60],['.v4 .sample .pl','row',60],['.v4 .ring','pop',60],['.v4 .marks .tag','pop',60],
  ['.v5 .arcs','wire'],['.v5 .nd','row',40],
  ['.v6 .dcol','row',50],['.v6 .chk','pop',60],
  ['.v7 .dcard>*','row',40],['.v7 .ticks i','pop',40],['.v7 .edge','edge',90],['.v7 .tl-r','row',40],['.v7 .plate','row'],
  ['.matrix>*','mat'],
  ['.v-lib .lib-row','row',40],['.v-lib .ld.on','pop',60],['.v-lib .lib-job>*','row',40],
  ['.v-tree .lr','row',40],['.v-tree .tags>*','pop',60],
  ['.v-fe .fe-chips>*','pop',60],['.v-fe .fe-row .dcol','row',60]
];
function cardSeq(card){
  card.__a&&card.__a.forEach(a=>a.cancel());card.__a=[];
  if(reduce())return;
  const vis_=$('.vis',card);if(!vis_)return;
  let t=0;
  for(const [sel,kind,gap=50] of CARD_SEQ){
    const els=$$(sel,vis_).filter(vis);if(!els.length)continue;
    let end=t;
    if(kind==='mat'){ /* matrix: grid of 5 columns, stagger by row */
      els.forEach((el,i)=>{const d=t+Math.floor(i/5)*40;card.__a.push(rise(el,{delay:d}));end=Math.max(end,d+240)})}
    else if(kind==='edge'){els.reverse().forEach((el,i)=>{const d=t+i*gap;card.__a.push(fx(el,[{opacity:0,transform:'translateY(6px)'},{opacity:+getComputedStyle(el).opacity,transform:'translateY(0)'}],{duration:240,delay:d,easing:E_OUT,fill:'backwards'}));end=Math.max(end,d+240)})}
    else if(kind==='wire'){card.__a.push(fx(els[0],[{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0 0 0)'}],{duration:360,delay:t,easing:E_OUT}));end=t+360}
    else els.forEach((el,i)=>{const d=t+i*gap;
      card.__a.push(kind==='row'?rise(el,{delay:d}):kind==='fade'?fadein(el,{delay:d,dur:240}):popin(el,{from:.96,delay:d}));end=Math.max(end,d+240)});
    t=end-120;
  }
}

function initPinned(sec){
  const wrap=$('.deck-wrap',sec),sticky=$('.deck-sticky',sec),sents=$$('.deck-sentinels>div',sec),n=sents.length,live=$('[data-deck-live]',sec);
  const track=$('[data-track]',sec),cards=$$('.card',sec),steps=$$('.step',sec),segs=$$('.sg',sec);
  const isDeck=!!track,pager=$('[data-pager]',sec),pill=pager&&$('.pill',pager),dots=$$('.pg',sec),prev=$('[data-prev]',sec),next=$('[data-next]',sec);
  const view=$('.deck-view',sec);
  const S={idx:0,nav:null,navT:0,pitch:0,tracked:false};
  const pinned=()=>getComputedStyle(sticky).position==='sticky';
  const geo=()=>{ /* active index from the sentinels' geometry: the one that holds the viewport's middle line */
    const wr=wrap.getBoundingClientRect(),mid=innerHeight/2-wr.top;
    const t1=sents[1]?sents[1].offsetTop:0,step=sents[1]?sents[1].offsetHeight:1;
    return clamp(Math.floor((mid-t1)/step)+1,0,n-1)};
  const stepPx=()=>sents[1]?sents[1].offsetHeight:innerHeight/2;
  const wrapTop=()=>wrap.getBoundingClientRect().top+scrollY;
  const title=i=>isDeck?($('.h3',cards[i])||{}).textContent:(segs[i]?$('.sg-l',segs[i]).textContent:'');
  /* track spring (M6) and pager pill spring (M9) */
  const sp=spring({ratio:1,response:.35}),ps=spring({ratio:.9,response:.28});
  if(isDeck){sp.frame(x=>{track.style.transform=`translate3d(${x}px,0,0)`});ps.frame(x=>{pill.style.transform=`translateX(${x}px)`})}
  const measure=()=>{if(isDeck&&cards[1])S.pitch=cards[1].offsetLeft-cards[0].offsetLeft};
  const minX=()=>-(n-1)*S.pitch;
  const setIndex=(i,{instant,velocity}={})=>{
    const was=S.idx;S.idx=i;
    if(isDeck){
      cards.forEach((c,k)=>{c.classList.toggle('is-active',k===i);c.classList.toggle('is-next',k===i+1)});
      if(pinned()){measure();const x=-i*S.pitch;
        if(instant||reduce()||!S.tracked){sp.jump(x);S.tracked=true}else sp.set(x,{velocity})}
      else{track.style.transform=''}
      if(pill){const px=i*24;pill.style.setProperty('--i',i);if(instant||reduce()||!pinned())ps.jump(px);else ps.set(px)}
      {const tier=cards[i].dataset.tier;$$('[data-legend] .chip').forEach(c=>c.classList.toggle('wh',!!tier&&c.dataset.tier===tier))}
      dots.forEach((d,k)=>k===i?d.setAttribute('aria-current','true'):d.removeAttribute('aria-current'));
      prev&&prev.setAttribute('aria-disabled',String(i===0));next&&next.setAttribute('aria-disabled',String(i===n-1));
      if(i!==was)cardSeq(cards[i]);
    }else stepChange(was,i);
  };
  /* ---- stepper (M14, M14b, M15) ---- */
  const rollNums=(step)=>$$('.bud',step).forEach((b,k)=>{const s=b.querySelector('.dm');if(!s)return;const val=s.getAttribute('aria-label'),cls=s.getAttribute('class').replace(/^dm\s*/,'');
    const host=document.createElement('span');host.className='dmroll';s.replaceWith(host);host.appendChild(dm('0',40,cls));setTimeout(()=>roll(host,val,40),360+k*80)});
  let stepAnims=[];
  function stepChange(a,b){
    stepAnims.forEach(x=>x.cancel());stepAnims=[];
    steps.forEach((s,k)=>{s.classList.toggle('is-active',k===b);s.classList.remove('is-leaving')});
    segs.forEach((g,k)=>{g.classList.toggle('on',k===b);g.setAttribute('aria-selected',String(k===b));g.tabIndex=k===b?0:-1});
    if(a===b||!pinned())return;
    const A=steps[a],B=steps[b];
    if(reduce()){stepAnims.push(...[$('.step-cap',B),$('.step-vis',B)].filter(Boolean).map(e=>fx(e,[{opacity:0},{opacity:1}],{duration:150,easing:'linear'})));return}
    const aV=$$('.step-cap,.step-vis',A),bV=$$('.step-cap,.step-vis',B);
    A.classList.add('is-leaving');
    const fwd=b>a;
    const out=aV.map(e=>e.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:fwd&&b===2?'translateY(-8px)':'translateY(0)'}],{duration:b===2?200:140,easing:b===2?E_OUT:E_IO,fill:'forwards'}));
    stepAnims.push(...out);out[0].onfinish=()=>{A.classList.remove('is-leaving');out.forEach(o=>o.cancel())};
    if(a===0&&b===1){ /* A to B: wires draw in from the slab side, agent cards and rows rise */
      const base=120;
      $$('.step-cap',B).forEach(e=>stepAnims.push(fx(e,[{opacity:0},{opacity:1}],{duration:200,delay:base,easing:E_IO})));
      const vis_=$('.sv',B);
      stepAnims.push(fx($('.slab',vis_),[{opacity:0},{opacity:1}],{duration:240,delay:base,easing:E_IO}));
      stepAnims.push(fx($$('.sw-wires',vis_).find(vis)||$('.sw-wires',vis_),[{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0 0 0)'}],{duration:240,delay:base+60,easing:E_IO}));
      $$('.agent',vis_).forEach((ag,k)=>stepAnims.push(fx(ag,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,delay:base+k*80,easing:E_IO})));
      $$('.sr-row',vis_).forEach((r,k)=>stepAnims.push(fx(r,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,delay:base+k*24,easing:E_IO})));
      $$('.meter',vis_).forEach((m,k)=>stepAnims.push(m.animate([{transform:'scaleX(0)'},{transform:`scaleX(${getComputedStyle(m,'::after').transform==='none'?1:1})`}],{duration:400,delay:base+240+k*80,easing:E_OUT,fill:'backwards',pseudoElement:'::after'})));
      rollNums(B);
    }else if(b===2||(a===2&&b!==2)){
      bV.forEach(e=>stepAnims.push(fx(e,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,delay:a===2?120:200,easing:E_OUT})));
      $$('.cr',B).forEach((r,k)=>stepAnims.push(fx(r,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,delay:200+k*40,easing:E_OUT})));
    }else{
      bV.forEach(e=>stepAnims.push(fx(e,[{opacity:0},{opacity:1}],{duration:240,delay:120,easing:E_IO})));
    }
    if(b===1)$$('.meter',B).forEach(m=>m.classList.add('go'));
  }
  /* ---- navigation: everything scrolls the page to wrapTop + i * step; the sentinels set the index ---- */
  const announce=()=>{if(live)live.textContent=`Step ${S.idx+1} of ${n}: ${title(S.idx)}`};
  const endNav=()=>{if(S.nav==null)return;S.nav=null;clearTimeout(S.navT);announce();sync()};
  const nav=(i,{velocity,announceIt=true}={})=>{
    if(!pinned())return;i=clamp(i,0,n-1);const step=stepPx(),top0=wrapTop();
    const target=clamp(top0+i*step,top0,top0+(n-1)*step);
    S.nav=announceIt?i:null;if(!announceIt){S.nav=i}
    setIndex(i,{velocity});
    const dist=Math.abs(scrollY-target);
    if(dist<2){endNav();return}
    scrollTo({top:target,behavior:reduce()?'auto':'smooth'});
    clearTimeout(S.navT);S.navT=setTimeout(endNav,1800);
  };
  const sync=()=>{if(!pinned())return;const i=geo();
    if(S.nav!=null){if(i===S.nav)endNav();return}
    if(i!==S.idx)setIndex(i)};
  new IntersectionObserver(sync,{rootMargin:'-50% 0px -50% 0px',threshold:[0,1]}).observe(sents[0]);
  sents.slice(1).forEach(s=>new IntersectionObserver(sync,{rootMargin:'-50% 0px -50% 0px',threshold:[0,1]}).observe(s));
  addEventListener('scrollend',()=>{if(S.nav!=null)endNav()});
  ['wheel','touchstart','mousedown'].forEach(ev=>addEventListener(ev,()=>{if(S.nav!=null){S.nav=null;clearTimeout(S.navT)}},{passive:true}));
  /* controls */
  dots.forEach(d=>d.addEventListener('click',()=>nav(+d.dataset.go)));
  segs.forEach(g=>g.addEventListener('click',()=>nav(+g.dataset.go)));
  prev&&prev.addEventListener('click',()=>{if(prev.getAttribute('aria-disabled')!=='true')nav(S.idx-1)});
  next&&next.addEventListener('click',()=>{if(next.getAttribute('aria-disabled')!=='true')nav(S.idx+1)});
  const keyer=e=>{const k=e.key;let i=null;
    if(k==='ArrowLeft'||k==='ArrowUp'&&!isDeck)i=S.idx-1;else if(k==='ArrowRight'||k==='ArrowDown'&&!isDeck)i=S.idx+1;else if(k==='Home')i=0;else if(k==='End')i=n-1;
    if(i==null||!pinned())return;e.preventDefault();i=clamp(i,0,n-1);nav(i);if(!isDeck&&segs[i])segs[i].focus({preventScroll:true})};
  if(isDeck){view.setAttribute('tabindex','0');view.addEventListener('keydown',keyer)}
  else $('[data-seg]',sec).addEventListener('keydown',keyer);
  /* skip link */
  $$('[data-skip]',sec).forEach(a=>a.addEventListener('click',e=>{
    const t=document.querySelector(a.getAttribute('href'));if(!t||!pinned())return;e.preventDefault();goto(t)}));
  /* ---- touch drag (M10) ---- */
  if(isDeck){
    let d=null;
    view.addEventListener('pointerdown',e=>{if(e.pointerType!=='touch'||!pinned()||d)return;measure();d={id:e.pointerId,x:e.clientX,y:e.clientY,base:sp.value,on:false,s:[[e.timeStamp,e.clientX]]}});
    view.addEventListener('pointermove',e=>{if(!d||e.pointerId!==d.id)return;
      const dx=e.clientX-d.x,dy=e.clientY-d.y;
      if(!d.on){if(Math.abs(dx)>10&&Math.abs(dx)>Math.abs(dy)){d.on=true;view.setPointerCapture(e.pointerId);if(S.nav!=null){S.nav=null;clearTimeout(S.navT)}}else return}
      d.s.push([e.timeStamp,e.clientX]);if(d.s.length>6)d.s.shift();
      if(reduce())return;
      let x=d.base+dx-Math.sign(dx)*10;
      if(x>0)x=rubber(x,cards[0].offsetWidth);else if(x<minX())x=minX()-rubber(minX()-x,cards[0].offsetWidth);
      sp.jump(x)});
    const end=e=>{if(!d||e.pointerId!==d.id)return;const was=d;d=null;if(!was.on)return;
      const a=was.s[0],b=was.s[was.s.length-1],v=b[0]>a[0]?(b[1]-a[1])/(b[0]-a[0])*1000:0;
      let i;
      if(reduce())i=clamp(S.idx+(Math.abs(b[1]-was.x)>40||Math.abs(v)>300?(v<0||b[1]<was.x?1:-1):0),0,n-1);
      else{const proj=sp.value+project(v);i=clamp(Math.round(-proj/S.pitch),0,n-1);if(i===S.idx&&Math.abs(v)<150)i=clamp(Math.round(-sp.value/S.pitch),0,n-1)}
      nav(i,{velocity:v})};
    view.addEventListener('pointerup',end);view.addEventListener('pointercancel',end);
  }
  /* geometry changes */
  const relayout=()=>{measure();if(isDeck){if(pinned()&&!reduce())sp.jump(-S.idx*S.pitch);else track.style.transform='';ps.jump(S.idx*24)}sync()};
  addEventListener('resize',relayout);
  RM.addEventListener('change',relayout);
  const init=()=>{measure();
    if(!isDeck){steps.forEach((s,k)=>s.classList.toggle('is-active',k===0));segs.forEach((g,k)=>{g.tabIndex=k===0?0:-1})}
    else{cards.forEach((c,k)=>{c.classList.toggle('is-active',k===0);c.classList.toggle('is-next',k===1)});setIndex(0,{instant:true})}
    if(pinned()){const i=geo();if(i!==0||S.idx!==0)setIndex(i,{instant:true})}};
  init();
  if(document.fonts)document.fonts.ready.then(relayout);
  /* the first card builds when the stage is in view */
  if(isDeck)new IntersectionObserver((es,io)=>{if(es.some(e=>e.isIntersecting)){io.disconnect();cardSeq(cards[S.idx])}},{threshold:.4}).observe(sticky);
  sec.__pin=S;sec.__nav=nav;
}
/* scroll to a section top, then focus its heading (skip links and dock links) */
function goto(target,{push}={}){
  const top=target.getBoundingClientRect().top+scrollY;
  scrollTo({top,behavior:reduce()?'auto':'smooth'});
  const h=target.querySelector('h2,h1');
  if(h){h.setAttribute('tabindex','-1');h.focus({preventScroll:true})}
  if(push&&target.id)history.replaceState(null,'','#'+target.id);
}

/* ---------- Ch2 routing (M11 to M13m) ---------- */
function initRouting(){
  const chips=$$('[data-chips] .chip');if(!chips.length)return;
  const bar=$('[data-chips]'),res=$('[data-result]'),card=$('.result-card'),tiers=$$('.tcard'),host=$('.rt-right');
  const TN={haiku:'Haiku',sonnet:'Sonnet',opus:'Opus'},DEF={haiku:['low',1],sonnet:['medium',2],opus:['high, xhigh for design and reviews',3]};
  const hue={haiku:'hue-moss',sonnet:'hue-indigo',opus:'hue-plum'};
  const ticks=(g,n)=>$$('.tk',g).forEach((t,i)=>t.classList.toggle('on',i<n));
  const rpoly=p=>{let d=`M${p[0][0].toFixed(1)} ${p[0][1].toFixed(1)}`;for(let i=1;i<p.length-1;i++){const[a,b,c]=[p[i-1],p[i],p[i+1]],l1=Math.hypot(b[0]-a[0],b[1]-a[1])||1,l2=Math.hypot(c[0]-b[0],c[1]-b[1])||1,r=Math.min(14,l1/2,l2/2);
      d+=`L${(b[0]-(b[0]-a[0])/l1*r).toFixed(1)} ${(b[1]-(b[1]-a[1])/l1*r).toFixed(1)}Q${b[0].toFixed(1)} ${b[1].toFixed(1)} ${(b[0]+(c[0]-b[0])/l2*r).toFixed(1)} ${(b[1]+(c[1]-b[1])/l2*r).toFixed(1)}`}
    const e=p[p.length-1];return d+`L${e[0].toFixed(1)} ${e[1].toFixed(1)}`};
  let wireEl=null;
  const drawWire=(chip,tc,animate)=>{
    const ph=$('[data-routing-wire]');if(!ph||!vis(ph)||!host)return;
    const hr=host.getBoundingClientRect(),c=chip.getBoundingClientRect(),t=tc.getBoundingClientRect(),tr=$('[data-tiers]').getBoundingClientRect();
    const pts=[[c.right-hr.left+2,c.top+c.height/2-hr.top],[-12,c.top+c.height/2-hr.top],[-12,tr.top-hr.top-24],[t.left+t.width/2-hr.left,tr.top-hr.top-24],[t.left+t.width/2-hr.left,t.top-hr.top+1]];
    const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]),L=Math.min(...xs)-6,T=Math.min(...ys)-6,W=Math.max(...xs)-L+6,H=Math.max(...ys)-T+6;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('class','wires rt-live');svg.setAttribute('aria-hidden','true');svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    svg.style.cssText=`left:${L}px;top:${T}px;width:${W}px;height:${H}px`;
    const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('class','wire');p.setAttribute('d',rpoly(pts.map(q=>[q[0]-L,q[1]-T])));svg.appendChild(p);
    const old=wireEl;wireEl=svg;host.appendChild(svg);
    if(!animate||reduce()){old&&old.remove();return}
    if(old){old.animate([{clipPath:'inset(0 0 0 0)'},{clipPath:'inset(0 0 0 100%)'}],{duration:140,easing:E_OUT,fill:'forwards'}).onfinish=()=>old.remove()}
    svg.animate([{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0 0 0)'}],{duration:240,delay:old?140:0,easing:E_OUT,fill:'backwards'});
  };
  const apply=(chip,{animate})=>{
    const {tier,agent,effort}=chip.dataset,prevTier=(tiers.find(t=>t.classList.contains('on'))||{}).dataset;
    chips.forEach(c=>{const on=c===chip;c.setAttribute('aria-pressed',String(on));c.classList.toggle('wh',on);c.tabIndex=on?0:-1});
    res.textContent=`${TN[tier]} · ${agent} · effort ${effort}`;
    let line='';
    tiers.forEach(t=>{const on=t.dataset.tier===tier;t.classList.toggle('on',on);
      $$('.arow',t).forEach(r=>{const s=on&&r.dataset.agent===agent;r.classList.toggle('sel',s);if(s)line=$('.al',r).textContent});
      const [lab,n]=DEF[t.dataset.tier];let L=lab,N=n;if(on&&effort==='xhigh'&&tier==='opus'){L='xhigh';N=4}
      ticks($('.gauge',t),N);$('.el',t).textContent=L});
    if(card&&vis(card)){
      const swap=()=>{card.className=`result-card m-card ${hue[tier]}`;$('.tn',card).textContent=TN[tier];$('.ra',card).textContent=agent;$('.t-body-s',card).textContent=line;
        ticks($('.gauge',card),tier==='opus'&&effort==='xhigh'?4:DEF[tier][1]);$('.el',card).textContent=tier==='opus'&&effort==='xhigh'?'xhigh':DEF[tier][0]};
      if(!animate)swap();else if(reduce()){swap();rmFade([card])}
      else card.animate([{opacity:1,transform:'translateX(0)'},{opacity:0,transform:'translateX(-24px)'}],{duration:100,easing:E_OUT,fill:'forwards'}).onfinish=function(){swap();this.cancel();card.animate([{opacity:0,transform:'translateX(24px)'},{opacity:1,transform:'translateX(0)'}],{duration:180,easing:E_OUT})}}
    drawWire(chip,tiers.find(t=>t.dataset.tier===tier),animate);
  };
  chips.forEach(c=>c.addEventListener('click',()=>{if(c.getAttribute('aria-pressed')!=='true')apply(c,{animate:true})}));
  bar.addEventListener('keydown',e=>{const i=chips.indexOf(document.activeElement);if(i<0)return;let j=null;
    if(e.key==='ArrowRight'||e.key==='ArrowDown')j=(i+1)%chips.length;else if(e.key==='ArrowLeft'||e.key==='ArrowUp')j=(i-1+chips.length)%chips.length;else if(e.key==='Home')j=0;else if(e.key==='End')j=chips.length-1;
    if(j==null)return;e.preventDefault();chips[j].focus()});
  const cur=()=>chips.find(c=>c.getAttribute('aria-pressed')==='true')||chips[4];
  apply(cur(),{animate:false});
  let w=innerWidth;addEventListener('resize',()=>{if(innerWidth!==w){w=innerWidth;apply(cur(),{animate:false})}});
  if(document.fonts)document.fonts.ready.then(()=>apply(cur(),{animate:false}));
  {const d=cur();if(d&&bar.scrollWidth>bar.clientWidth)bar.scrollLeft+=d.getBoundingClientRect().left-bar.getBoundingClientRect().left-16}
  window.__routing={select:i=>apply(chips[i],{animate:true})};
}

/* ---------- dock: lit tab (M23), install pill (M24), anchors ---------- */
function initDock(){
  const dock=$('[data-dock]');if(!dock)return;
  const tabs=$$('.tab',dock),pillEl=$('[data-pill]',dock),puck=$('[data-puck]',dock);
  /* the lit layer is a copy of the tabs, clipped to the lit tab's rect */
  puck.setAttribute('aria-hidden','true');puck.replaceChildren(...tabs.map(t=>{const c=document.createElement('span');c.className='tab';c.innerHTML=t.innerHTML;return c}));
  const rect=t=>{const L=t.offsetLeft,R=dock.clientWidth-(t.offsetLeft+t.offsetWidth);return`inset(6px ${R}px 6px ${L}px round 26px)`};
  let lit=null,clip=null;
  const place=(tab,animate)=>{
    tabs.forEach(t=>t===tab?t.setAttribute('aria-current','true'):t.removeAttribute('aria-current'));
    const was=lit;lit=tab;
    if(!tab){puck.getAnimations().forEach(a=>a.cancel());if(animate&&!reduce()&&was)puck.animate([{opacity:1},{opacity:0}],{duration:200,easing:E_OUT,fill:'forwards'});else puck.style.opacity='0';return}
    const to=rect(tab);
    if(!was||!animate||reduce()){puck.getAnimations().forEach(a=>a.cancel());puck.style.opacity='1';puck.style.clipPath=to;clip=to;return}
    const from=getComputedStyle(puck).clipPath;
    puck.style.opacity='1';puck.getAnimations().forEach(a=>a.cancel());
    puck.style.clipPath=to;puck.animate([{clipPath:from==='none'?clip:from},{clipPath:to}],{duration:240,easing:E_OUT});clip=to;
  };
  const byId={how:'how',routing:'how',context:'how',reviews:'how',guards:'how',design:'design',memory:'memory',evolution:'memory',app:'app'};
  const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){const id=e.target.dataset.chapter,t=byId[id]?tabs.find(x=>x.dataset.tab===byId[id]):null;place(t,true)}})},{rootMargin:'-45% 0px -45% 0px'});
  $$('[data-chapter]').forEach(s=>io.observe(s));
  addEventListener('resize',()=>{if(lit){puck.style.clipPath=rect(lit)}});
  /* install pill */
  let shown=true,watch=new Set();
  const setPill=show=>{
    if(show===shown||!pillEl)return;shown=show;
    const gone=dock.dataset.pill==='gone';
    if(show){pillEl.getAnimations().forEach(a=>a.cancel());const w=gone?pillEl.offsetWidth:0;dock.dataset.pill='';const cw=pillEl.offsetWidth+4;
      if(!reduce()){dock.animate([{transform:`translateX(calc(-50% - ${cw/2}px))`},{transform:'translateX(-50%)'}],{duration:200,easing:E_OUT});
        pillEl.animate([{opacity:0,transform:'translateX(-10px)'},{opacity:1,transform:'translateX(0)'}],{duration:200,easing:E_OUT,fill:'backwards'})}
      else rmFade([pillEl]);
      if(lit)puck.style.clipPath=rect(lit)}
    else{const cw=pillEl.offsetWidth+4,done=()=>{if(shown)return;dock.dataset.pill='gone';if(!reduce())dock.animate([{transform:`translateX(calc(-50% + ${cw/2}px))`},{transform:'translateX(-50%)'}],{duration:200,easing:E_OUT});if(lit)puck.style.clipPath=rect(lit)};
      if(reduce()){pillEl.animate([{opacity:1},{opacity:0}],{duration:150,easing:'linear',fill:'forwards'}).onfinish=function(){done();this.cancel()};return}
      pillEl.animate([{opacity:1,transform:'translateX(0)'},{opacity:0,transform:'translateX(-10px)'}],{duration:140,easing:E_OUT,fill:'forwards'}).onfinish=function(){done();this.cancel()}}
  };
  const pio=new IntersectionObserver(es=>{es.forEach(e=>e.isIntersecting?watch.add(e.target):watch.delete(e.target));setPill(watch.size===0)},{threshold:.2});
  $$('[data-install],#install').forEach(f=>pio.observe(f));
  setPill(true);
  /* the whole dock steps away while an install field sits in its band, so it never covers Copy (M1) */
  const fields=$$('[data-install]');let away=false,tick=0;
  const checkAway=()=>{tick=0;const band=innerHeight-dock.offsetHeight-32;
    const hit=fields.some(f=>{const r=f.getBoundingClientRect();return r.bottom>band&&r.top<innerHeight});
    if(hit===away)return;away=hit;dock.classList.toggle('is-away',hit);hit?dock.setAttribute('inert',''):dock.removeAttribute('inert')};
  const sched=()=>{if(!tick)tick=requestAnimationFrame(checkAway)};
  addEventListener('scroll',sched,{passive:true});addEventListener('resize',sched);checkAway();
  /* anchors: smooth scroll and focus the heading */
  $$('a[href^="#"]').forEach(a=>{const id=a.getAttribute('href').slice(1);if(!id||a.hasAttribute('data-skip'))return;
    a.addEventListener('click',e=>{const t=document.getElementById(id);if(!t)return;e.preventDefault();goto(t,{push:true})})});
}

/* ---------- copy and the help line (M27) ---------- */
function initCopy(){
  const coarse=mq('(pointer:coarse)'),mac=/Mac|iPhone|iPad/.test(navigator.platform||navigator.userAgent);
  const live=document.createElement('p');live.className='sr';live.setAttribute('aria-live','polite');document.body.appendChild(live);
  $$('[data-install]').forEach(f=>{
    const btn=$('[data-copy]',f),cmd=$('[data-cmd]',f),help=document.getElementById(btn.getAttribute('aria-describedby'))||$('[data-help]'),lab=$('.cp-l',btn),dn=$('.cp-d',btn);
    let t1=0,t2=0;
    const say=(txt,ms)=>{clearTimeout(t2);if(!reduce()&&txt)help.animate([{opacity:0},{opacity:1}],{duration:180,easing:E_OUT});help.textContent=txt;if(txt)t2=setTimeout(()=>{help.textContent=''},ms)};
    const swap=done=>{
      btn.classList.toggle('is-done',done);
      if(reduce())return;
      const el=done?dn:lab;el.animate([{opacity:0,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:E_OUT});
      if(done)$('.ic',dn).animate([{transform:'scale(.92)'},{transform:'scale(1)'}],{duration:180,easing:E_OUT})};
    btn.addEventListener('click',async()=>{
      const text=cmd.textContent.trim();
      try{
        if(!navigator.clipboard||!navigator.clipboard.writeText)throw 0;
        await navigator.clipboard.writeText(text);
        swap(true);clearTimeout(t1);t1=setTimeout(()=>swap(false),1600);
        if(coarse.matches){live.textContent='';setTimeout(()=>{live.textContent='Copied to clipboard'},30);say('Paste it in Claude Code on your computer.',4000)}
        else say('Copied to clipboard',1600);
      }catch{
        const r=document.createRange();r.selectNodeContents(cmd);const s=getSelection();s.removeAllRanges();s.addRange(r);
        say(coarse.matches?'Text selected. Use Copy from the menu.':mac?'Press ⌘C to copy':'Press Ctrl+C to copy',4000);
      }
    });
  });
}

/* ---------- Ch9 app moments (M28) ---------- */
function initApp(){
  const app=$('[data-app]');if(!app)return;
  const tabs=$$('.mt',app),moms=$$('.moment',app);
  let steps={};fetch('media/app/steps.json').then(r=>r.ok?r.json():{}).then(j=>{steps=j||{}}).catch(()=>{});
  const M={};moms.forEach(m=>{const k=m.dataset.m;M[k]={el:m,vid:$('video.clip',m),img:$('img.still',m),btn:$('[data-clipbtn]',m),sts:$$('.st',m),still:1,n:3,state:'idle',err:k==='D'}});
  let cur='A',inView=false,timer=0;
  const src=k=>`media/app/${k}-${M[k].still}.webp`;
  const setStep=(k,i)=>M[k].sts.forEach((s,j)=>s.classList.toggle('cur',j===i));
  const label=(k,t)=>{const b=M[k].btn;if(b)b.setAttribute('aria-label',t)};
  const showStill=(k,n,fade)=>{const m=M[k];m.still=n;if(fade&&!reduce()){m.img.animate([{opacity:.0},{opacity:1}],{duration:180,easing:'linear'})}else if(fade)rmFade([m.img]);m.img.src=src(k);setStep(k,Math.min(n-1,m.sts.length-1))};
  const stop=k=>{const m=M[k];clearTimeout(timer);if(m.vid&&!m.vid.paused)m.vid.pause()};
  const bindClip=k=>{const m=M[k];if(!m.vid||m.bound)return;m.bound=true;
    m.vid.addEventListener('error',()=>{m.err=true;m.vid.hidden=true;m.img.hidden=false;m.n=3;label(k,'Next still')});
    m.vid.addEventListener('timeupdate',()=>{const t=steps[k]||[];let i=0;t.forEach((s,j)=>{if(m.vid.currentTime>=s)i=j});setStep(k,i)});
    m.vid.addEventListener('ended',()=>{m.state='ended';label(k,'Replay the clip')});
    m.vid.addEventListener('playing',()=>{m.state='playing';label(k,'Pause the clip')});
    m.vid.addEventListener('pause',()=>{if(m.state==='playing'&&!m.vid.ended)m.state='paused'});
  };
  const attach=k=>{const m=M[k];if(!m.vid||m.vid.getAttribute('src')||m.err)return;m.vid.poster=`media/app/${k}-1.webp`;m.vid.src=`media/app/${k}.mp4`;m.vid.load();bindClip(k)};
  const playClip=k=>{const m=M[k];if(!m.vid||m.err||reduce())return;attach(k);m.img.hidden=true;m.vid.hidden=false;if(m.vid.ended||m.state==='ended')m.vid.currentTime=0;const p=m.vid.play();if(p&&p.catch)p.catch(()=>{m.err=true;m.vid.hidden=true;m.img.hidden=false;label(k,'Next still')})};
  const cycleD=()=>{const m=M.D;clearTimeout(timer);const step=()=>{if(m.still>=3){m.state='ended';label('D','Replay the stills');return}timer=setTimeout(()=>{showStill('D',m.still+1,true);step()},2400)};m.state='playing';label('D','Pause the stills');step()};
  const start=k=>{
    const m=M[k];if(reduce()){label(k,'Next still');return}
    if(k==='D'){showStill('D',1,false);cycleD();return}
    playClip(k)};
  const select=(k,focus)=>{
    if(k===cur)return;const from=M[cur],to=M[k];stop(cur);
    tabs.forEach(t=>{const on=t.dataset.moment===k;t.classList.toggle('on',on);t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;if(on){if(focus)t.focus();const bar=t.parentElement;bar.scrollTo({left:Math.max(0,t.offsetLeft-16),behavior:reduce()?'auto':'smooth'})}});
    from.el.classList.remove('on');to.el.classList.add('on');cur=k;
    const kids=[...to.el.children];if(!reduce()){kids.forEach(k=>k.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:E_OUT}))}else rmFade(kids);
    to.still=1;to.img.hidden=false;to.img.src=src(k);if(to.vid)to.vid.hidden=true;setStep(k,0);to.state='idle';if(reduce()||to.err)label(k,'Next still');
    if(inView)start(k);
  };
  tabs.forEach(t=>t.addEventListener('click',()=>select(t.dataset.moment,false)));
  $('[data-moments]',app).addEventListener('keydown',e=>{const i=tabs.findIndex(t=>t.classList.contains('on'));let j=null;
    if(e.key==='ArrowRight')j=(i+1)%tabs.length;else if(e.key==='ArrowLeft')j=(i-1+tabs.length)%tabs.length;else if(e.key==='Home')j=0;else if(e.key==='End')j=tabs.length-1;
    if(j==null)return;e.preventDefault();select(tabs[j].dataset.moment,true)});
  moms.forEach(el=>{const k=el.dataset.m,m=M[k];if(!m.btn)return;
    m.btn.addEventListener('click',()=>{
      if(reduce()||m.err){const n=m.n||3;showStill(k,m.still%n+1,true);label(k,'Next still');return}
      if(k==='D'){if(m.state==='playing'){clearTimeout(timer);m.state='paused';label(k,'Resume the stills')}else{if(m.still>=3||m.state==='ended'){showStill('D',1,true)}cycleD()}return}
      if(m.state==='playing'){m.vid.pause();m.state='paused';label(k,'Resume the clip')}else if(m.state==='paused'){m.vid.play();}else{m.vid.currentTime=0;playClip(k)}})});
  const io=new IntersectionObserver(es=>{es.forEach(e=>{const was=inView;inView=e.isIntersecting&&e.intersectionRatio>=.5||e.isIntersecting&&e.intersectionRatio>=.5;
    if(inView&&!was){const m=M[cur];if(m.state==='idle')start(cur);else if(m.state==='paused'&&m.vid&&!m.vid.ended&&cur!=='D')m.vid.play()}
    else if(!inView&&was){const m=M[cur];if(m.state==='playing'){if(cur==='D'){clearTimeout(timer);m.state='paused'}else{m.vid.pause();m.state='paused'}}}})},{threshold:[0,.5]});
  io.observe(app);
  const pre=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){if(!reduce())Object.keys(M).forEach(k=>{if(k!=='D')attach(k)});pre.disconnect()}})},{rootMargin:'100% 0px'});
  pre.observe(app);
  document.addEventListener('visibilitychange',()=>{const m=M[cur];if(document.hidden){if(m.state==='playing'){if(cur==='D')clearTimeout(timer);else m.vid.pause();m.state='paused'}}else if(inView&&m.state==='paused'&&cur!=='D'&&m.vid)m.vid.play()});
  RM.addEventListener&&RM.addEventListener('change',()=>{Object.keys(M).forEach(k=>stop(k));start(cur)});
  Object.keys(M).forEach(k=>{if(k!=='D')label(k,reduce()?'Next still':'Replay the clip')});
}

/* ---------- init ---------- */
function initFbm(){
  const ok=CSS.supports('backdrop-filter','blur(1px)')||CSS.supports('-webkit-backdrop-filter','blur(1px)');
  if(!ok)root.classList.add('fbm');
}
function boot(){
  window.__vos=true;
  initFbm();initFit();initReveals();initRouter();initRouting();initLoop();initLadder();initDial();initGuards();
  $$('[data-deck],[data-stepper]').forEach(initPinned);
  initDock();initCopy();initApp();
  if(location.hash.length>1){const t=document.getElementById(decodeURIComponent(location.hash.slice(1)));t&&setTimeout(()=>goto(t,{push:false}),60)}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
